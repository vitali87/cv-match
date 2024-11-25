from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
import uvicorn
import tempfile
import os
from docx import Document
from anthropic import Anthropic
import json
import shutil
import time
from starlette.background import BackgroundTask
import logging
import subprocess
from dotenv import load_dotenv
import pypdf
import random
import string

# Load environment variables and configure logging
load_dotenv(override=True)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

CONFIG = {
    "PATHS": {"TEMPLATES": "templates", "OUTPUT": "output"},
    "PDF_SETTINGS": {"MARGIN": "0.75in", "FONT_SIZE": "10pt", "LINE_SPACING": "1.15"},
}

app = FastAPI()
templates = Jinja2Templates(directory=CONFIG["PATHS"]["TEMPLATES"])


# Initialize Anthropic client with better error handling
def init_anthropic_client():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not found in environment variables")
        raise ValueError("ANTHROPIC_API_KEY not found in environment variables")
    try:
        client = Anthropic(api_key=api_key)
        # Test the client with a simple request
        client.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=10,
            messages=[{"role": "user", "content": "test"}],
        )
        return client
    except Exception as e:
        logger.error(f"Failed to initialize Anthropic client: {e}")
        raise ValueError(f"Failed to initialize Anthropic client: {e}")


try:
    client = init_anthropic_client()
except Exception as e:
    logger.error(f"Failed to initialize application: {e}")
    raise


def extract_text(file_path: str, file_type: str) -> str:
    """Extract text from PDF or DOCX files."""
    try:
        if file_type == "pdf":
            with open(file_path, "rb") as file:
                pdf = pypdf.PdfReader(file)
                return " ".join(page.extract_text() for page in pdf.pages)
        else:  # docx
            doc = Document(file_path)
            return " ".join(paragraph.text for paragraph in doc.paragraphs)
    except Exception as e:
        logger.error(f"Error extracting text: {e}")
        raise HTTPException(status_code=400, detail=f"Error extracting text: {str(e)}")


def parse_cv_with_llm(cv_text: str, job_description: str) -> dict:
    """Use Claude to parse and structure CV content."""
    try:
        system_prompt = """You are a CV parsing expert. Your task is to parse the CV text and return ONLY a JSON object.

Important Instructions:

- **Do not alter any personal contact details**, including names, emails, phone numbers, addresses, and **URLs**. These should be extracted exactly as they appear in the CV.
- **Do not generate or infer any new contact information**.
- The JSON MUST be valid and follow this exact structure:

{
    "profile": {
        "name": "string",
        "title": "string",
        "summary": "string",
        "contact": {
            "email": "string",
            "phone": "string",
            "location": "string"
        },
        "links": [
            {
                "platform": "string",  // e.g., "GitHub", "LinkedIn", "Google Scholar"
                "url": "string"
            }
        ]
    },
    "work_experience": [
        {
            "title": "string",
            "company": "string",
            "date": "string",
            "achievements": ["string"]
        }
    ],
    "education": [
        {
            "degree": "string",
            "institution": "string",
            "date": "string",
            "details": ["string"]
        }
    ],
    "skills": [
        {
            "category": "string",
            "items": ["string"]
        }
    ],
    "languages": [
        {
            "language": "string",
            "proficiency": "string"
        }
    ]
}"""

        response = client.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=4096,
            temperature=0,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"""Parse this CV and optimize it for the following job description by changing or adding wording to match the keywords mentioned in the description.

**Important Instructions:**

- **Do not alter any personal contact details**, including names, emails, phone numbers, addresses, and **URLs**. These should be extracted exactly as they appear in the CV.
- **Do not generate or infer any new contact information**.
- **Ensure that skills required in the job description are deduced from my CV without providing any false information**.
- **Return ONLY the JSON object**, no additional text or explanations.

Job Description:
{job_description}

CV Text:
{cv_text}""",
                }
            ],
        )

        # Get the response text and parse it
        response_text = response.content[0].text.strip()
        return json.loads(response_text)

    except Exception as e:
        logger.error(f"Error parsing CV with LLM: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def generate_markdown(cv_data: dict) -> str:
    """Convert structured CV data to markdown format."""
    sections = [
        """---
header-includes:
    - '\\usepackage{xcolor}'
    - '\\usepackage[colorlinks=true,urlcolor=blue,linkcolor=blue]{hyperref}'
---

"""
    ]

    # Profile Section with improved formatting
    if profile := cv_data.get("profile"):
        # Name and Title with clean spacing
        name = profile.get("name", "").upper()
        title = profile.get("title", "")
        sections.append(f"# {name}")
        sections.append(f"_{title}_\n")

        # Contact details in a clean, single line
        if contact := profile.get("contact"):
            contact_items = []
            if email := contact.get("email"):
                contact_items.append(f"{email}")
            if phone := contact.get("phone"):
                contact_items.append(f"{phone}")
            if location := contact.get("location"):
                contact_items.append(f"{location}")
            if contact_items:
                sections.append(f"{' • '.join(contact_items)}\n")

        # Professional links with LaTeX formatting
        if links := profile.get("links"):
            link_items = []
            for link in links:
                platform = link.get("platform", "")
                url = link.get("url", "").strip()

                logger.info(f"BEFORE - Platform: {platform}, Original URL: {url}")

                # Remove any @ symbol if it exists at the start of the URL
                url = url.lstrip("@")

                logger.info(f"AFTER - Platform: {platform}, Processed URL: {url}")
                logger.info(f"Generated LaTeX link: \\href{{{url}}}{{{platform}}}")

                link_items.append(f"\\href{{{url}}}{{{platform}}}")
            if link_items:
                sections.append(f"{' • '.join(link_items)}\n")

        # Professional summary with proper spacing
        if summary := profile.get("summary"):
            sections.append(f"{summary}\n")

        sections.append("---\n")

    # Work Experience
    if work_exp := cv_data.get("work_experience"):
        sections.append("## Professional Experience\n")
        for job in work_exp:
            sections.append(f"### {job['title']} | {job['company']}")
            sections.append(f"_{job['date']}_")
            for achievement in job.get("achievements", []):
                sections.append(f"* {achievement}")
            sections.append("")

    # Education
    if education := cv_data.get("education"):
        sections.append("## Education\n")
        for edu in education:
            sections.append(f"### {edu['degree']} | {edu['institution']}")
            sections.append(f"_{edu['date']}_")
            for detail in edu.get("details", []):
                sections.append(f"* {detail}")
            sections.append("")

    # Skills with improved error handling
    if skills := cv_data.get("skills"):
        sections.append("## Technical Skills\n")
        for skill in skills:
            category = skill.get("category", "")
            items = skill.get("items", [])
            # Handle cases where items might be dictionaries or strings
            formatted_items = []
            for item in items:
                if isinstance(item, dict):
                    # If item is a dictionary, extract the relevant value
                    # Adjust the key based on your actual data structure
                    formatted_items.append(
                        str(
                            item.get("name", "")
                            or item.get("skill", "")
                            or item.get("value", "")
                        )
                    )
                else:
                    formatted_items.append(str(item))
            sections.append(f"**{category}:** {', '.join(formatted_items)}")
            sections.append("")

    # Languages
    if languages := cv_data.get("languages"):
        sections.append("## Languages\n")
        lang_items = []
        for lang in languages:
            if isinstance(lang, dict):
                language = lang.get("language", "")
                proficiency = lang.get("proficiency", "")
                lang_items.append(f"**{language}**: {proficiency}")
        sections.append(", ".join(lang_items))

    return "\n".join(sections)


def create_pdf(markdown_content: str, output_path: str) -> None:
    """Convert markdown to PDF using pandoc."""
    try:
        md_file = output_path[:-4] + ".md"
        with open(md_file, "w") as f:
            f.write(markdown_content)

        subprocess.run(
            [
                "pandoc",
                md_file,
                "-o",
                output_path,
                "--pdf-engine=xelatex",
                "-V",
                f"geometry:margin={CONFIG['PDF_SETTINGS']['MARGIN']}",
                "-V",
                f"fontsize={CONFIG['PDF_SETTINGS']['FONT_SIZE']}",
                "-V",
                f"linestretch={CONFIG['PDF_SETTINGS']['LINE_SPACING']}",
                "--standalone",
                "--variable",
                "colorlinks=true",
                "--variable",
                "urlcolor=blue",
                "--variable",
                "linkcolor=blue",
            ],
            check=True,
        )

        os.remove(md_file)
    except Exception as e:
        logger.error(f"Error creating PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def generate_random_code(length: int = 6) -> str:
    """Generate a random alphanumeric code."""
    try:
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))
    except Exception as e:
        logger.error(f"Error generating random code: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload")
async def upload_files(
    cv_file: UploadFile = File(...),
    job_description: str = Form(...),
    scholar_url: str = Form(None),
):
    """Process uploaded CV and generate optimized version."""
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Received scholar_url from frontend: {scholar_url}")

        # Save uploaded file
        file_path = os.path.join(temp_dir, cv_file.filename)
        with open(file_path, "wb") as f:
            content = await cv_file.read()
            f.write(content)

        # Extract text
        file_type = "pdf" if cv_file.filename.lower().endswith(".pdf") else "docx"
        cv_text = extract_text(file_path, file_type)

        # Process with LLM
        cv_data = parse_cv_with_llm(cv_text, job_description)

        logger.info("Links before override:")
        for link in cv_data["profile"]["links"]:
            logger.info(f"Platform: {link['platform']}, URL: {link['url']}")

        # Override Google Scholar URL if provided
        if scholar_url:
            logger.info(f"Attempting to override with scholar_url: {scholar_url}")
            # Remove any existing Google Scholar link
            cv_data["profile"]["links"] = [
                link
                for link in cv_data["profile"]["links"]
                if link["platform"] != "Google Scholar"
            ]
            # Add the new Google Scholar link
            cv_data["profile"]["links"].append(
                {"platform": "Google Scholar", "url": scholar_url.strip()}
            )

            logger.info("Links after override:")
            for link in cv_data["profile"]["links"]:
                logger.info(f"Platform: {link['platform']}, URL: {link['url']}")

        # Generate markdown
        markdown_content = generate_markdown(cv_data)

        # Log the first few lines of markdown content to see what URLs made it through
        logger.info("First 500 characters of markdown content:")
        logger.info(markdown_content[:500])

        # Create output file with random code
        output_dir = os.path.join(os.path.dirname(__file__), CONFIG["PATHS"]["OUTPUT"])
        os.makedirs(output_dir, exist_ok=True)

        random_code = generate_random_code()
        timestamp = int(time.time())
        output_filename = f"cv_{timestamp}_{random_code}.pdf"
        output_path = os.path.join(output_dir, output_filename)

        create_pdf(markdown_content, output_path)

        # URL encode the filename and add both filename and filename* parameters
        encoded_filename = output_filename.encode("utf-8").decode("latin-1")
        headers = {
            "Content-Disposition": f"attachment; filename=\"{encoded_filename}\"; filename*=UTF-8''{encoded_filename}",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        return FileResponse(
            path=output_path,
            media_type="application/pdf",
            filename=output_filename,
            headers=headers,
            background=BackgroundTask(lambda: os.remove(output_path)),
        )


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
