# Syllabus Builder

A Streamlit app that lets users edit syllabus fields, generate a new LaTeX syllabus, and download the compiled PDF.

## Features

- Upload an existing syllabus PDF to auto-fill the Theory fields (title, course details, objectives, outcomes, units, text/reference books); lab syllabi are detected automatically and fill the Lab fields.
- Also recognises Project/Internship syllabi that use a phase-based "Weekly Work Plan" instead of units and carry no books (only the signatory box is added)
- Edit Theory and Lab pages independently from the browser
- Auto-correct (sidebar switch) fixes grammar, extra spaces, spacing around punctuation, broken lines, typed numbering (`1.`, `CO1:`, `•`) and unit headings (`Unit 3 - x` → `UNIT III: X`) as you edit; **✨ Clean up all fields** does the same for everything already in the boxes, e.g. old PDF imports. Every change can be undone
- Save your work as a small `.json` draft and load it later (the app does not keep work after a refresh or when Streamlit Cloud sleeps)
- Keep Lab content separate from the Theory PDF with no side-panel include toggle
- Turn bottom signatories on or off per page
- Generate and download separate `.tex` and PDF files for Theory and Lab using `latexmk`
- Use the actual Times New Roman system font via `fontspec` when compiling with XeLaTeX or LuaLaTeX; on Linux hosts without it (e.g. Streamlit Cloud) the genuine font is downloaded once from Microsoft's free Core Fonts package and installed automatically
- Ready to push to GitHub and deploy on Streamlit Community Cloud

## Files

- `app.py` - Streamlit UI
- `syllabus_generator.py` - syllabus data model, LaTeX rendering, and PDF compilation
- `pdf_extractor.py` - parses an uploaded syllabus PDF into the data model fields
- `draft_io.py` - saves/loads drafts as JSON (tested in `test_drafts.py`)
- `text_cleanup.py` - rule-based line/space/heading clean-up (tested in `test_text_cleanup.py`)
- `main.tex` - existing standalone LaTeX source retained as a reference

## Run locally

1. Make sure `streamlit` and a TeX distribution with `latexmk` are installed.
2. To compile the LaTeX source with the actual Times New Roman system font, use **XeLaTeX** or **LuaLaTeX**.
3. Start the app:

   streamlit run app.py

## Deploy from GitHub

1. Push this folder to a GitHub repository.
2. In Streamlit Community Cloud, create a new app from that repository.
3. Set the entrypoint to `app.py`.
4. Ensure the deployment environment includes LaTeX tools if PDF compilation is required.
5. Use XeLaTeX or LuaLaTeX for PDF generation if you want Times New Roman via `fontspec`.

## Notes

- The app escapes LaTeX-sensitive characters entered by users.
- PDF generation depends on `latexmk` and XeLaTeX being available on the host machine (`packages.txt` installs them on Streamlit Cloud).
- **Times New Roman licence:** `fonts/times32.exe` is Microsoft's original, unmodified "Core fonts for the Web" installer (SHA-256 `db56595e…f807ab`), which its [EULA](https://corefonts.sourceforge.net/eula.htm) allows to be redistributed free of charge. On Linux hosts without Times New Roman (e.g. Streamlit Cloud) the app unpacks it once with `cabextract` into `~/.fonts`. Do not replace it with extracted `.ttf` files, which may not be redistributed. This is the 2000-era version of the font, so a few newer symbols (for example the ₹ sign) are missing.
- Grammar check uses the local LanguageTool engine when Java is installed, otherwise the public LanguageTool API (text is sent to languagetool.org). "Apply All" skips the book lists because author names are often flagged wrongly.
- PDF import (`pdfplumber`) reads text-based PDFs only; scanned/image-only PDFs are not supported. Every field is best-effort, so review the auto-filled values before generating.
