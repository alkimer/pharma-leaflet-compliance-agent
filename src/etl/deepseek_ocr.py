"""
Wrapper around the local DeepSeek-OCR model (Apple Silicon / CUDA / CPU).

Only used from `etl.pdf_ocr_pipeline`, as a fallback for scanned PDFs. Loading the
model is expensive, so it is instantiated once per run and reused for every page.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = "Convert the text in this image to markdown format."
MAX_NEW_TOKENS = 4096


def resolve_model_path(model_dir: str | Path) -> str:
    """
    Resolve the model's real path.

    If `model_dir` is a Hugging Face Hub cache (blobs/refs/snapshots), returns the
    path of the snapshot pointed at by refs/main.
    """
    model_path = Path(model_dir)
    refs_main = model_path / "refs" / "main"
    if refs_main.exists():
        snapshot_path = model_path / "snapshots" / refs_main.read_text().strip()
        if snapshot_path.exists():
            return str(snapshot_path)
    return str(model_path)


class DeepSeekOCR:
    """OCR engine based on DeepSeek-OCR (deepseek_vl_v2 architecture)."""

    def __init__(
        self,
        model_dir: str | Path,
        device: str = "mps",
        dtype: str = "bfloat16",
        trust_remote_code: bool = True,
    ):
        """
        Args:
            model_dir: Model folder (or HF Hub cache).
            device: "mps" (Apple Silicon), "cuda" or "cpu".
            dtype: Preferred precision when the device supports it.
            trust_remote_code: Required: the model defines its own architecture.
        """
        import torch

        self.device = device
        self.model_dir = resolve_model_path(model_dir)
        logger.info(f"Ruta del modelo resuelta: {self.model_dir}")

        if device == "mps":
            # MPS does not support bfloat16 reliably.
            self.torch_dtype = torch.float32
        elif dtype == "bfloat16" and torch.cuda.is_available() and torch.cuda.is_bf16_supported():
            self.torch_dtype = torch.bfloat16
        elif dtype == "float16":
            self.torch_dtype = torch.float16
        else:
            self.torch_dtype = torch.float32

        logger.info(f"Inicializando DeepSeekOCR en {self.device} ({self.torch_dtype})")
        self._load_processor(trust_remote_code)
        self._load_model(trust_remote_code)

    def _load_processor(self, trust_remote_code: bool) -> None:
        """Load the processor; on failure, fall back to the bare tokenizer."""
        from transformers import AutoProcessor, AutoTokenizer

        try:
            self.processor = AutoProcessor.from_pretrained(
                self.model_dir, trust_remote_code=trust_remote_code
            )
            self.tokenizer = getattr(self.processor, "tokenizer", None)
            logger.info("AutoProcessor cargado")
        except Exception as e:
            logger.warning(f"No se pudo cargar AutoProcessor: {e}; probando con AutoTokenizer")
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(
                    self.model_dir, trust_remote_code=trust_remote_code
                )
                self.processor = None
            except Exception as inner:
                raise RuntimeError(
                    f"No se pudo cargar ni el processor ni el tokenizer: {inner}"
                ) from inner

    def _load_model(self, trust_remote_code: bool) -> None:
        """Load the model, moving it to MPS after instantiating it on CPU."""
        import torch
        from transformers import AutoModel

        logger.info(f"Cargando el modelo desde {self.model_dir}")
        if self.device == "mps":
            # On MPS it is better to instantiate on CPU and move afterwards:
            # device_map="mps" tends to run out of memory during loading.
            self.model = AutoModel.from_pretrained(
                self.model_dir,
                trust_remote_code=trust_remote_code,
                torch_dtype=torch.float16,
                device_map="cpu",
                low_cpu_mem_usage=True,
            ).eval()
            try:
                self.model = self.model.to("mps")
                logger.info("Modelo movido a MPS")
            except Exception as e:
                logger.warning(f"No se pudo mover el modelo a MPS, se usa CPU: {e}")
        else:
            self.model = AutoModel.from_pretrained(
                self.model_dir,
                trust_remote_code=trust_remote_code,
                torch_dtype=self.torch_dtype,
                device_map=self.device if self.device != "cpu" else None,
                low_cpu_mem_usage=True,
            ).eval()
        logger.info("Modelo cargado")

    def _build_inputs(self, pil_image: Any, prompt: str) -> Any:
        """Prepare the input tensors in whichever format the processor accepts."""
        if self.processor is None:
            raise RuntimeError("No hay processor de imágenes disponible")

        try:
            conversation = [
                {
                    "role": "User",
                    "content": "<image_placeholder>\n" + prompt,
                    "images": [pil_image],
                },
                {"role": "Assistant", "content": ""},
            ]
            return self.processor(
                conversations=conversation,
                images=[pil_image],
                force_batchify=True,
                return_tensors="pt",
            ).to(self.model.device)
        except Exception as e:
            logger.debug(f"Formato de conversación no aceptado ({e}); probando formato simple")
            return self.processor(text=prompt, images=pil_image, return_tensors="pt").to(
                self.model.device
            )

    def recognize(
        self,
        image_path: str | Path,
        output_dir: Optional[str | Path] = None,
        prompt: Optional[str] = None,
        save_results: bool = True,
    ) -> Dict[str, Any]:
        """
        Recognise the text in an image and return it as markdown.

        Args:
            image_path: Input image.
            output_dir: Where to write `result.mmd` (defaults to next to the image).
            prompt: Alternative prompt for the model.
            save_results: When False, nothing is written to disk.

        Returns:
            {"success": bool, "text": str, "result_file": str, "stats": {...}}
            or {"success": False, "error": str} if something failed.
        """
        import torch
        from PIL import Image

        start_time = time.time()
        image_path = Path(image_path)
        if not image_path.exists():
            return {"success": False, "error": f"Imagen no encontrada: {image_path}"}

        prompt = prompt or DEFAULT_PROMPT

        try:
            pil_image = Image.open(image_path).convert("RGB")
            logger.debug(f"Procesando {image_path.name} ({pil_image.size[0]}x{pil_image.size[1]})")

            inputs = self._build_inputs(pil_image, prompt)

            tokenizer = self.tokenizer or getattr(self.processor, "tokenizer", None)
            if tokenizer is None:
                return {"success": False, "error": "No hay tokenizer disponible"}

            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=MAX_NEW_TOKENS,
                    do_sample=False,
                    use_cache=True,
                    pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                )

            text = tokenizer.decode(outputs[0].cpu().tolist(), skip_special_tokens=True)
            if prompt in text:
                text = text.split(prompt)[-1].strip()

            result_file = None
            if save_results:
                target_dir = Path(output_dir) if output_dir else image_path.parent
                target_dir.mkdir(parents=True, exist_ok=True)
                result_file = target_dir / "result.mmd"
                result_file.write_text(text, encoding="utf-8")

            elapsed = time.time() - start_time
            return {
                "success": True,
                "text": text,
                "result_file": str(result_file) if result_file else None,
                "stats": {"total_time_seconds": elapsed, "image_size": pil_image.size},
            }

        except Exception as e:
            logger.exception(f"Error en el OCR de {image_path.name}")
            return {"success": False, "error": str(e)}
