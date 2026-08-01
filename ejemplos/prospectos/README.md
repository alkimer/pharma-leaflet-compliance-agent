# Sample leaflets

The files in this folder are **synthetic**: fictional products with invented data, written
only so the pipeline can be run end to end out of the box.

- `IBUPROFENO-DEMO.md` — fictional over-the-counter leaflet ("IBUFICTOL"), deliberately
  incomplete so that step 3 finds unmet rules and step 4 has something to fix.

No real, client-owned or unpublished leaflet ships with this repository. To try it on a real
document, point the pipeline at your own file:

```bash
python run_pipeline.py --prospecto /path/to/your-leaflet.pdf --no-interactivo
```
