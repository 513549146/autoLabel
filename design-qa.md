# Design QA

## Source and implementation

- Source visual truth: the user-approved desktop workbench image, copied to `assets/workbench-reference-v1.png`.
- Implementation: a clean-room PySide6 workbench in `gui/qt_workbench.py`.
- Legacy status: `main.py` now starts the Qt workbench; the former Tk page classes are not imported, created, or embedded by the running application.

## Verified screenshot

- Captured an off-screen Qt screenshot at 1420 × 900: `qt-workbench-preview-v7.png`.
- Verified directly from that capture: left project workflow, three-dot project menu, a single aligned command strip, large canvas, rounded label inspector, compact thumbnail surface, and progress footer all render as independent Qt layouts.
- Registered a Chinese UI font explicitly from the local Windows font set; this fixed the CJK missing-glyph regression exposed by the first off-screen capture.
- Replaced the initial system/3D toolbar glyphs with cropped transparent line-art from the project icon assets; the latest preview is `qt-workbench-preview-v7.png`.
- Exercised the image-rendering path with a real `QPixmap` fixture; this verifies the PySide6 `QSize.scaled` exception is resolved and the label inspector's flattened selector still has a visible chevron.

## Functional wiring retained

- New/open project creates or restores `images`, `annotations`, and `yolo_dataset` directories.
- Every project root now has a portable `autolabel.project.json` descriptor. Opening that project restores its internal image and annotation folders directly into the review workspace; source files are imported into the project only once.
- Project image status, categories, and project paths are backed by the standard-library SQLite database `autolabel.db` at the project root. Legacy `.autolabel-project.json` files are imported automatically on first open and left in place as a backup.
- The review canvas loads actual project images, VOC XML boxes, category choices, thumbnails, and review status transitions.
- The review canvas renders the original image through an integer-sized high-quality scale cache (rather than a cached thumbnail), supports wheel zoom, drag pan, double-click fit, select-and-move, rectangle creation, delete, undo/redo, and writes edits back to the project's VOC XML through 保存.
- The review command bar has one non-duplicated command set only. ImageGen-produced transparent selection and rectangle-drawing icons distinguish the two editing modes; polygon drawing is omitted because the project currently persists VOC rectangles only.
- The inspector exposes multi-select category visibility filters separately from the selected box's editable label. Hidden categories do not alter the color or styling of visible boxes; each new rectangle asks for its label immediately.
- Approved-image cards use a fixed 224 × 202 px top-left aligned grid so a small approved set does not stretch one image across the workspace.
- The app-level SQLite `workspace.db` records up to 12 recent project roots and the last opened project; the project menu exposes the recents, and startup restores the last valid project automatically.
- Opening a project (or staging folders from 自动标注) scans matching VOC XML files and places their images directly into 待审核, while preserving an existing 已通过 result.
- The 自动标注 primary action now loads the selected local detector on a background worker, writes its VOC XML output into the active project's `annotations` folder, records completed images as 待审核, and then opens the review queue without freezing the UI.
- Each 已通过 card now has a destructive-style “删除记录” action. It is deliberately non-destructive to source data: it clears the approval state by moving the image back to 待审核 and leaves both the image and XML intact.
- Auto, approved, and export stages are separate Qt pages; no old Tk tab is reused.
- The frameless-window resize gutter now listens across child widgets and immediately restores the regular cursor after the pointer leaves an edge.

## Remaining comparison limit

- The repository currently has no usable image inside its configured `images/` directory, so the populated canvas, colored annotation boxes, and thumbnail state cannot be compared at the same state as the source visual.
- The empty-state composition has been captured and reviewed. Populate a project image folder to complete the final populated-state comparison.

final result: blocked
