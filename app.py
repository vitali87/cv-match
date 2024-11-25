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
from typing import Optional

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
            model="claude-3-5-sonnet-20241022",
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


def parse_cv_with_llm(
    cv_text: str, job_description: str, scholar_url: Optional[str] = None
) -> dict:
    """Use Claude to parse and enhance CV content."""
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

        user_content = f"""Parse this CV and enhance it to align closely with the following job description. Focus on highlighting and emphasizing the relevant skills, experiences, and qualifications that match the job requirements. Use keywords and phrases from the job description where appropriate to showcase the alignment.

**Important Instructions:**

- **Do not alter any personal contact details**, including names, emails, phone numbers, addresses, and **URLs**. These should be extracted exactly as they appear in the CV.
- **Do not generate or infer any new contact information**.
- **Ensure that the skills and experiences required in the job description are clearly reflected in the CV content, based on the information provided in the CV. Do not fabricate or include any information that is not present in the CV.**
- **Rephrase or enhance existing descriptions in the CV to use keywords and phrases from the job description, where applicable, to strengthen the alignment.**
- **Return ONLY the JSON object**, no additional text or explanations.

Job Description:
{job_description}

CV Text:
{cv_text}"""

        if scholar_url:
            user_content += f"\nGoogle Scholar URL:\n{scholar_url}"

        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4096,
            temperature=0,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": user_content,
                }
            ],
        )

        # Ensure that response content exists
        if not response.content or not response.content[0].text.strip():
            logger.error("Empty response received from Anthropic API.")
            raise HTTPException(
                status_code=500, detail="Empty response received from language model."
            )

        # Get the response text and parse it
        response_text = response.content[0].text.strip()
        try:
            parsed_response = json.loads(response_text)
            return parsed_response
        except json.JSONDecodeError as json_err:
            logger.error(f"JSON decoding failed: {json_err}")
            logger.error(f"Response Text: {response_text}")
            raise HTTPException(
                status_code=500, detail="Invalid JSON response from language model."
            )

    except Exception as e:
        logger.error(f"Error parsing CV with LLM: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def generate_markdown(cv_data: dict) -> str:
    """Convert structured CV data to markdown format."""
    sections = []

    # Profile Section
    profile = cv_data.get("profile", {})
    sections.append(f"# {profile.get('name', 'Your Name')}")
    sections.append(f"**{profile.get('title', 'Your Title')}**\n")

    # Contact Information
    contact = profile.get("contact", {})
    contact_info = f"- Email: {contact.get('email', 'email@example.com')}\n- Phone: {contact.get('phone', '123-456-7890')}\n- Location: {contact.get('location', 'Your Location')}"
    sections.append(contact_info + "\n")

    sections.append(f"{profile.get('summary', 'A brief summary about yourself.')}\n")

    # Links (e.g., GitHub, LinkedIn, Google Scholar)
    links = profile.get("links", [])
    if links:
        sections.append("## Links")
        for link in links:
            platform = link.get("platform", "")
            url = link.get("url", "")
            if platform and url:
                sections.append(f"- [{platform}]({url})")
        sections.append("")  # Add an empty line for spacing

    # Work Experience
    work_experience = cv_data.get("work_experience", [])
    if work_experience:
        sections.append("## Work Experience")
        for experience in work_experience:
            title = experience.get("title", "Job Title")
            company = experience.get("company", "Company Name")
            date = experience.get("date", "Date Range")
            achievements = experience.get("achievements", [])
            sections.append(f"### {title} at {company}")
            sections.append(f"*{date}*")
            for achievement in achievements:
                sections.append(f"- {achievement}")
            sections.append("")  # Add an empty line for spacing

    # Education
    education = cv_data.get("education", [])
    if education:
        sections.append("## Education")
        for edu in education:
            degree = edu.get("degree", "Degree")
            institution = edu.get("institution", "Institution Name")
            date = edu.get("date", "Date Range")
            details = edu.get("details", [])
            sections.append(f"### {degree}, {institution}")
            sections.append(f"*{date}*")
            for detail in details:
                sections.append(f"- {detail}")
            sections.append("")  # Add an empty line for spacing

    # Skills
    skills = cv_data.get("skills", [])
    if skills:
        sections.append("## Skills")
        for skill in skills:
            category = skill.get("category", "Category")
            items = skill.get("items", [])
            sections.append(f"### {category}")
            sections.append(", ".join(items))
            sections.append("")  # Add an empty line for spacing

    # Languages
    languages = cv_data.get("languages", [])
    if languages:
        sections.append("## Languages")
        lang_items = []
        for lang in languages:
            language = lang.get("language", "")
            proficiency = lang.get("proficiency", "")
            if language and proficiency:
                lang_items.append(f"**{language}**: {proficiency}")
        sections.append(", ".join(lang_items))
        sections.append("")  # Add an empty line for spacing

    return "\n".join(sections)


def create_pdf(markdown_content: str, output_path: str) -> None:
    """Convert markdown to PDF using pandoc with XeLaTeX for Unicode support."""
    try:
        md_file = output_path[:-4] + ".md"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        # Use XeLaTeX to handle Unicode characters with wider margins
        pandoc_command = [
            "pandoc",
            md_file,
            "-o",
            output_path,
            "--pdf-engine=xelatex",
            "-V",
            "geometry:margin=0.5in",
            "--variable",
            "colorlinks=true",
            "--variable",
            "linkcolor=blue",
            "--variable",
            "urlcolor=blue"
        ]

        result = subprocess.run(pandoc_command, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"Pandoc error: {result.stderr}")
            raise HTTPException(
                status_code=500, detail=f"Error creating PDF: {result.stderr}"
            )

        # Optionally, remove the markdown file after successful PDF creation
        os.remove(md_file)

    except Exception as e:
        logger.error(f"Error creating PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating PDF: {str(e)}")


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
    scholar_url: Optional[str] = Form(None),
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
        cv_data = parse_cv_with_llm(cv_text, job_description, scholar_url)

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

        # Simplify the Content-Disposition header
        headers = {
            "Content-Disposition": f'attachment; filename="{output_filename}"',
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
