import logging
import uuid
from pathlib import Path

from src.models.md2pdf import ConvertFileType
from src.utils.file_convert import convert_markdown_text_to_pdf_file
from src.utils.oss import (
    DownloadUrlToTempFileRequest,
    PdfUploadResult,
    download_url_to_temp_file,
    get_filename_from_url,
    temp_dir,
    upload_file_to_oss,
)

logger = logging.getLogger(__name__)


class Md2PdfService:
    async def convert_markdown_to_pdf_and_upload(
        self,
        *,
        url: str,
        file_type: ConvertFileType = ConvertFileType.pdf,
    ) -> PdfUploadResult:
        """Download markdown from URL, convert to PDF file, upload to OSS, and return upload result."""
        # Extract filename from URL and change extension to .pdf
        original_filename = get_filename_from_url(url)
        pdf_filename = Path(original_filename).stem + '.pdf'

        tmp_id = uuid.uuid4().hex[:8]
        tmp_path = f"./tmp/md2pdf_{tmp_id}"
        async with temp_dir(tmp_path) as tdir:
            # Download markdown
            download_req = DownloadUrlToTempFileRequest(
                url=url, temp_dir_path=str(tdir), filename='input.md'
            )
            download_resp = await download_url_to_temp_file(download_req)
            markdown_path = Path(download_resp.local_path)

            # Read markdown and convert to PDF file
            markdown_text = markdown_path.read_text(encoding='utf-8', errors='replace')
            pdf_path = tdir / f"output_{tmp_id}.pdf"
            
            # Convert to PDF (logos are loaded automatically from assets folder)
            convert_markdown_text_to_pdf_file(
                markdown_text, pdf_path
            )

            # Upload PDF file to OSS with filename from URL
            upload_result = await upload_file_to_oss(pdf_path, filename=pdf_filename)
            return upload_result
