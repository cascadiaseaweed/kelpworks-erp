# Lab requisition templates

Token-ready copies of the lab forms used by **Samples -> Cart -> Create requisitions**. They are the labs' own
documents with `{{placeholders}}` dropped in (the originals in `QAQC/2_Procedures/04_Forms_Templates` are untouched).

Upload one per lab in **Admin -> Labs & analyses -> Template**.

| File | Lab setup |
|---|---|
| `Food Assure Chain Of Custody - KelpWorks template.docx` | Tick "Also generate a sample spreadsheet" (the form says "see attached spreadsheet"). Analyses = the tests the form lists, e.g. `Yeast & Mold (CFU/g)`, `Aerobic Plate Count (CFU/g)`, `Indicator Organisms (MPN/g): Fecal Coliforms`, `Indicator Organisms (MPN/g): Salmonella spp`. |
| `SGS Sample Submission Request Form - KelpWorks template.docx` | One row per sample in both sample tables. Analyses with an optional *Method / spec* (e.g. `Mineral Scan up to 12 (see notes for specifics)` / `ICP-MS`). |

Customer contact on the FoodAssure form: `{{customer_phone}}` and `{{customer_email_1}}` .. `{{customer_email_5}}` (set under Admin -> Requisition contact details, overridable in the Samples cart; blank emails print nothing).

Placeholders: `{{req_number}} {{date}} {{date_long}} {{po_number}} {{po_check}} {{company}} {{lab_name}} {{lab_contact}}
{{lab_email}} {{lab_phone}} {{lab_address}} {{processing_lot}} {{run_date}} {{product}} {{requested_by}}
{{requested_by_email}} {{sample_count}} {{analyses}} {{notes}}`. A table row containing `{{sample.*}}` repeats per sample
(`n id report_description stage type description container collected analyses methods location notes`,
`{{sample.check:Analysis name}}` = checkbox). A paragraph containing `{{analysis.name}}` (`code method count n`) repeats per requested test.
