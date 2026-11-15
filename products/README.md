# Processed products (static mode and the stored demo field)

In `INVESTIGATION_MODE=live` (default) real requests run the pipeline and these folders are not used for them;
`GET /api/investigations/demo-001` still returns this stored field. In `static` mode every request returns it.

Each investigation is one folder: `products/<id>/manifest.json` plus the PNGs it references.
The server returns `manifest.json` from `GET /api/investigations/<id>` exactly as written, and serves
`products/<id>/<name>.png` as `/products/<id>/<name>.png`.

## What is here now

`demo-001/` holds **development placeholders** (128x128 PNGs, `data_mode: "development_fixture"`), so the
stack runs end to end. They are not SPHEREx data.

## Installing the real frames

1. Delete everything in `demo-001/`.
2. Copy in the two real frame PNGs and the science team's `manifest.json`, unedited.
3. Make sure each `image_url` in the manifest is `/products/demo-001/<file>.png`.
4. If there is no validated difference product, the manifest should say `"difference": null`.
   Do not keep a `difference.png` from the placeholders.
5. No restart is needed. The server reads the manifest on every request.

The server logs a warning at startup if `data_mode` is not `archive_data`. If a manifest says `complete` but a
referenced PNG is missing, the endpoint returns `status: "failed"`, `error_code: "products_missing"`, and still
includes every original warning.
