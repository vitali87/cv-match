# CV Matcher

A smart CV/Resume tailoring application that uses vector similarity search to match your experience with job requirements.

## Features

- Upload multiple CVs/Resumes (PDF or DOCX format)
- Upload a CV template (DOCX format)
- Input job description
- Get a tailored CV based on the most relevant experiences matching the job description

## Installation

1. Clone the repository
2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

1. Start the server:
```bash
python app.py
```

2. Open your browser and navigate to `http://localhost:8000`

3. Upload your documents:
   - Select one or more CV/Resume files (PDF or DOCX)
   - Upload a CV template (DOCX)
   - Paste the job description
   - Click "Generate Tailored CV"

4. Download your tailored CV

## How it works

1. The application uses the Sentence Transformers model to convert text into vectors
2. USearch is used to create an efficient vector similarity index
3. When you provide a job description, the app finds the most relevant sections from your input documents
4. A new CV is generated using your template and the most relevant content

## Requirements

- Python 3.8+
- See requirements.txt for all Python dependencies
