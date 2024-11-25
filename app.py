from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
import uvicorn
from typing import List
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
The JSON MUST be valid and follow this exact structure:
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
                    "content": f"""Parse this CV and optimize it for the following job description. Return ONLY the JSON object, no additional text or explanations.

Job Description:
{job_description}

CV Text:
{cv_text}""",
                }
            ],
        )

        # Debug logging
        logger.info(
            f"Claude Response: {response.content[0].text[:500]}..."
        )  # Log first 500 chars

        # Try to clean the response before parsing
        response_text = response.content[0].text.strip()
        # Remove any markdown code block indicators if present
        response_text = response_text.replace("```json", "").replace("```", "").strip()

        try:
            parsed_data = json.loads(response_text)
            # Validate required fields
            required_fields = [
                "profile",
                "work_experience",
                "education",
                "skills",
                "languages",
            ]
            for field in required_fields:
                if field not in parsed_data:
                    raise ValueError(f"Missing required field: {field}")
            return parsed_data
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error. Response text: {response_text}")
            logger.error(f"JSON error details: {str(e)}")
            raise ValueError(f"Failed to parse JSON response: {str(e)}")

    except Exception as e:
        logger.error(f"Error parsing CV with LLM: {str(e)}")
        logger.error(f"CV Text length: {len(cv_text)}")
        logger.error(f"Job Description length: {len(job_description)}")
        raise HTTPException(
            status_code=500, detail={"message": "Failed to process CV", "error": str(e)}
        )


def generate_markdown(cv_data: dict) -> str:
    """Convert structured CV data to markdown format."""
    sections = []

    # Profile Section
    if profile := cv_data.get("profile"):
        name = profile.get("name", "")
        title = profile.get("title", "")
        sections.append(f"# {name}")
        sections.append(f"## {title}\n")

        if summary := profile.get("summary"):
            sections.append(f"{summary}\n")

        if contact := profile.get("contact"):
            contact_items = []
            if email := contact.get("email"):
                contact_items.append(f"📧 {email}")
            if phone := contact.get("phone"):
                contact_items.append(f"📱 {phone}")
            if location := contact.get("location"):
                contact_items.append(f"📍 {location}")
            if contact_items:
                sections.append(" | ".join(contact_items) + "\n")

        if links := profile.get("links"):
            link_items = []
            for link in links:
                platform = link.get("platform", "")
                url = link.get("url", "")
                link_items.append(f"[{platform}]({url})")
            if link_items:
                sections.append(" | ".join(link_items) + "\n")

        sections.append("---\n")  # Horizontal line after profile

    # Work Experience
    if work_exp := cv_data.get("work_experience"):
        sections.append("# Work Experience\n")
        for job in work_exp:
            sections.append(f"## {job['title']} | {job['company']} | {job['date']}")
            for achievement in job.get("achievements", []):
                sections.append(f"* {achievement}")
            sections.append("")

    # Education
    if education := cv_data.get("education"):
        sections.append("# Education\n")
        for edu in education:
            sections.append(
                f"## {edu['degree']} - {edu['institution']} | {edu['date']}"
            )
            for detail in edu.get("details", []):
                sections.append(f"* {detail}")
            sections.append("")

    # Skills
    if skills := cv_data.get("skills"):
        sections.append("# Skills\n")
        for skill in skills:
            sections.append(f"## {skill['category']}")
            for item in skill.get("items", []):
                sections.append(f"* {item}")
            sections.append("")

    # Languages
    if languages := cv_data.get("languages"):
        sections.append("# Languages\n")
        for lang in languages:
            sections.append(f"* {lang['language']}: {lang.get('proficiency', '')}")

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
            ],
            check=True,
        )

        os.remove(md_file)
    except Exception as e:
        logger.error(f"Error creating PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload")
async def upload_files(
    cv_file: UploadFile = File(...), job_description: str = Form(...)
):
    """Process uploaded CV and generate optimized version."""
    with tempfile.TemporaryDirectory() as temp_dir:
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

        # Generate markdown
        markdown_content = generate_markdown(cv_data)

        # Create output file
        output_dir = os.path.join(os.path.dirname(__file__), CONFIG["PATHS"]["OUTPUT"])
        os.makedirs(output_dir, exist_ok=True)

        output_filename = f"optimized_cv_{int(time.time())}.pdf"
        output_path = os.path.join(output_dir, output_filename)

        create_pdf(markdown_content, output_path)

        return FileResponse(
            output_path,
            media_type="application/pdf",
            filename=output_filename,
            background=BackgroundTask(lambda: os.remove(output_path)),
        )


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
