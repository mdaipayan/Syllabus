# Syllabus Builder

A Streamlit app that lets users edit syllabus fields, generate a new LaTeX syllabus, and download the compiled PDF.

## Features

- Upload an existing syllabus PDF to auto-fill the Theory fields (title, course details, objectives, outcomes, units, text/reference books); lab syllabi are detected automatically and fill the Lab fields.
- Also recognises Project/Internship syllabi that use a phase-based "Weekly Work Plan" instead of units and carry no books (only the signatory box is added)
- Edit Theory and Lab pages independently from the browser
- Keep Lab content separate from the Theory PDF with no side-panel include toggle
- Turn bottom signatories on or off per page
- Generate and download separate `.tex` and PDF files for Theory and Lab using `latexmk`
- Use the actual Times New Roman system font via `fontspec` when compiling with XeLaTeX or LuaLaTeX; on Linux hosts without it (e.g. Streamlit Cloud) the genuine font is downloaded once from Microsoft's free Core Fonts package and installed automatically
- Ready to push to GitHub and deploy on Streamlit Community Cloud

## Files

- `app.py` - Streamlit UI
- `syllabus_generator.py` - syllabus data model, LaTeX rendering, and PDF compilation
- `pdf_extractor.py` - parses an uploaded syllabus PDF into the data model fields
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
- **Times New Roman licence:** on Linux the app downloads `times32.exe` from Microsoft's "Core fonts for the Web" (via SourceForge, the same source as Debian's `ttf-mscorefonts-installer`) and unpacks it with `cabextract`. Running the app on such a host means accepting the [Microsoft Core Fonts EULA](https://corefonts.sourceforge.net/eula.htm). The font files are never stored in this repository. This is the 2000-era version of the font, so a few newer symbols (for example the ₹ sign) are missing.
- Grammar check uses the local LanguageTool engine when Java is installed, otherwise the public LanguageTool API (text is sent to languagetool.org). "Apply All" skips the book lists because author names are often flagged wrongly.
- PDF import (`pdfplumber`) reads text-based PDFs only; scanned/image-only PDFs are not supported. Every field is best-effort, so review the auto-filled values before generating.
