# 📞 IVR Data Cleaner

A Streamlit web application that cleans and processes Interactive Voice Response (IVR) survey data. Handles both simple and complex multi-layer branching IVR flows.

## Features

- **Multiple data input methods**: Upload ZIP file, individual CSVs, or paste Google Drive links
- **Advanced Script Parsing**: Automatically extracts questions and answer mappings from PDF/DOCX scripts, featuring strict regex boundaries to prevent misclassification of embedded question text.
- **Likert Matrix Support**: Intelligently pairs overarching contextual questions with individual sub-items (e.g., "Sila tekan 1 untuk berpuas hati... [Polis]").
- **Multi-layer branching support**: Handles complex IVR flows with skip logic, redirects, and mutually exclusive paths by unpivoting positional data into dedicated flow columns.
- **Column merging**: Automatically merges columns with the same core question (e.g., "Di parlimen manakah anda?" across different flows) into a single cohesive column.
- **Interactive Skip Logic UI**: Features a hybrid auto-detect and human-in-the-loop dropdown to isolate respondents screened out early (e.g., "Are you a voter?" → Ya/Tidak).
- **Incomplete response removal**: Uses "Soalan terakhir" (last question) as completion indicator while respecting branch-specific nulls.
- **Inline editing**: Fix unmapped values and edit question/answer mappings directly in the app.
- **Excel export**: Exports cleaned data with separate sheets for main survey and skipped respondents.

## How It Works

### Step 1: Load Data
Upload your IVR CSV files via:
- **ZIP file** (recommended for multiple files)
- **Individual CSV files**
- **Google Drive file links** (one per line, files must be shared as "Anyone with the link")

### Step 2: Upload IVR Script
Upload the IVR call script document (PDF or DOCX). The app parses:
- Questions associated with each call flow
- Answer choices ("Tekan N untuk ...")
- Routing information ("Tekan X untuk Y Call flow M")
- Multi-item Likert blocks and their overarching context instructions.

You can edit parsed questions and answer mappings before proceeding.

### Step 3: Rename & Map Columns
The app automatically:
- Isolates and separates screening flows (Skip Logic) using a smart auto-detect algorithm, with a manual override dropdown for edge cases.
- Extracts positional column data into strict `FlowNo_X` columns to prevent data overwriting.
- Renames columns to the core question text from the script (stripping "Soalan N." prefixes).
- Maps `FlowNo_X=Y` values to readable answer text.
- Merges parallel branch columns into single variables.
- Allows inline fixing of any unmapped stray values.

### Step 4: Sanity Check & Export
- View data summary, column details, and value counts
- Adjust completeness threshold slider to control how strict the cleaning is
- Detects potential issues (unmapped values, high null columns)
- Download cleaned data as Excel (with separate sheet for skipped respondents)

## Installation

### Prerequisites
- Python 3.8+
- pip

### Setup

```bash
# Clone the repository
git clone [https://github.com/AstroImad/IVR-Data-Cleaner.git](https://github.com/AstroImad/IVR-Data-Cleaner.git)
cd IVR-Data-Cleaner

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run app.py

The app will open at `http://localhost:8501`.

## Project Structure

```
ivr-cleaner/
├── app.py              # Main Streamlit application (UI & flow)
├── parsers.py          # PDF/DOCX script parser
├── cleaning.py         # Data loading, cleaning & transformation logic
├── requirements.txt    # Python dependencies
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## Supported IVR Script Formats

The parser handles various IVR script layouts:

### Simple IVR (e.g., Negeri Sembilan)
```
Soalan pertama, adakah anda mengundi? Call flow 2
Tekan 1 untuk Ya
Tekan 2 untuk Tidak
```

### Multi-layer Branching IVR (e.g., Johor)
```
Soalan kedua. Di parlimen manakah anda mengundi? Call flow 3
Tekan 1 untuk Segamat, Sekijang, Labis, Ledang dan Bakri. Call flow 4
Tekan 2 untuk Sri Gading, Batu Pahat... Call flow 5
Tekan 6 untuk Lain-lain. Call flow 24
```

### Multi-item Sub-questions & Likert Scales (e.g., Hulu Selangor)
```
Soalan ketiga, Saya akan senaraikan beberapa pihak berkuasa.
Bomba                         tekan 1 hingga 3 Call flow 5
Klinik Kesihatan Kerajaan     tekan 1 hingga 3 Call flow 6
Majlis Perbandaran (MPHS)     tekan 1 hingga 3 Call flow 7
```

## Completeness Threshold
```
The slider in Step 4 controls how strictly incomplete responses are removed:

| Threshold | Behavior |
|-----------|----------|
| **1.0** (default) | Only keep respondents who answered the last question or have 100% of active columns filled |
| **0.8** | Keep respondents with 80%+ of questions answered |
| **0.5** | Keep respondents with 50%+ of questions answered (lenient) |
| **0.0** | Keep all respondents (only drop fully empty rows) |

**Note**: Respondents who answered "Lain-lain" (Others) are automatically redirected to the survey end. With threshold 1.0, these may need manual review if they didn't reach the last question column.

## Dependencies

- `streamlit` - Web application framework
- `pandas` - Data manipulation
- `numpy` - Numerical operations
- `gdown` - Google Drive file downloads
- `pdfplumber` - PDF text extraction
- `python-docx` - DOCX document parsing
- `openpyxl` - Excel file generation
- `requests` - HTTP requests

## License

This project is for internal use.
