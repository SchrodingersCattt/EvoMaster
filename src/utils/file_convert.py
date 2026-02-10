from enum import Enum
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import html
import re
import urllib.request
import urllib.parse

import markdown
import pymupdf
from PIL import Image
from weasyprint import CSS, HTML

# Cache CSS objects to improve performance
@lru_cache(maxsize=1)
def _get_css_stylesheets(project_root: str, assets_dir: str) -> tuple[CSS, CSS]:
    """Get cached CSS stylesheets."""
    styles = CSS(filename=Path(assets_dir) / 'styles.css')
    katex = CSS(filename=Path(assets_dir) / 'katex.min.css')
    return styles, katex


def _generate_aigc_tag() -> str:
    """Generate AIGC metadata tag for PDF."""
    import json
    return json.dumps(
        {
            'Label': 'DPtechnology_AIGC',
            'ContentProducer': 'DPtechnology_MatMaster',
            'ProduceID': uuid4().hex,
            'ReservedCode1': '',
            'ContentPropagator': 'DPtechnology_MatMaster',
            'PropagateID': uuid4().hex,
            'ReservedCode2': '',
            'ContentFeature': '深度调研',
        },
        ensure_ascii=False,
    )


def _download_image(url: str, temp_dir: Path) -> Path:
    """Download image from URL to temporary directory.
    
    Args:
        url: Image URL to download
        temp_dir: Temporary directory to save the image
        
    Returns:
        Path to the downloaded image file
    """
    try:
        # Parse URL to get filename
        parsed_url = urllib.parse.urlparse(url)
        filename = Path(parsed_url.path).name
        if not filename or '.' not in filename:
            # Generate filename from URL if not available
            filename = f"image_{uuid4().hex[:8]}.png"
        
        # Download image
        image_path = temp_dir / filename
        urllib.request.urlretrieve(url, image_path)
        return image_path
    except Exception as e:
        # If download fails, return original URL
        raise ValueError(f"Failed to download image from {url}: {e}")


def _process_blockquote_code_blocks(markdown_text: str) -> str:
    """Process blockquote code blocks (e.g., > ```json) to ensure proper rendering.
    
    Converts blockquote-wrapped code blocks to proper HTML structure that can be
    correctly rendered in PDF. This handles cases where code blocks are nested
    inside blockquotes, which standard markdown parsers may not handle correctly.
    
    Args:
        markdown_text: Original markdown text
        
    Returns:
        Processed markdown text with blockquote code blocks converted to HTML
    """
    lines = markdown_text.split('\n')
    processed_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Check if this line starts a blockquote code block: > ```
        if re.match(r'^>\s*```(\w+)?\s*$', line):
            # Extract language if present
            lang_match = re.match(r'^>\s*```(\w+)?\s*$', line)
            language = lang_match.group(1) or '' if lang_match else ''
            
            # Start building the code block content
            code_lines = []
            i += 1
            
            # Collect code block content (lines starting with > or just content)
            while i < len(lines):
                current_line = lines[i]
                
                # Check for end of code block: > ``` or ```
                if re.match(r'^>\s*```\s*$', current_line) or current_line.strip() == '```':
                    # Found end of code block
                    code_content = '\n'.join(code_lines)
                    # Remove leading '> ' from code lines if present
                    code_content = re.sub(r'^>\s?', '', code_content, flags=re.MULTILINE)
                    
                    # Escape HTML special characters in code content
                    code_content = html.escape(code_content)
                    
                    # Convert to HTML blockquote with code block (paragraph-style)
                    # Use <pre><code> inside blockquote for proper code formatting
                    lang_attr = f' class="language-{language}"' if language else ''
                    html_block = f'<blockquote><pre><code{lang_attr}>{code_content}</code></pre></blockquote>'
                    processed_lines.append(html_block)
                    i += 1
                    break
                else:
                    # Remove leading '> ' if present, keep the content
                    cleaned_line = re.sub(r'^>\s?', '', current_line)
                    code_lines.append(cleaned_line)
                    i += 1
            else:
                # Reached end of file without closing code block, append as-is
                processed_lines.append(line)
                i += 1
        else:
            processed_lines.append(line)
            i += 1
    
    return '\n'.join(processed_lines)


def _process_markdown_images(markdown_text: str, temp_dir: Path) -> tuple[str, list[Path]]:
    """Process markdown text to download images and replace URLs with local paths.
    
    Args:
        markdown_text: Original markdown text
        temp_dir: Temporary directory to save images
        
    Returns:
        Tuple of (processed markdown text, list of downloaded image paths)
    """
    downloaded_images = []
    
    # Pattern to match markdown images: ![alt](url)
    image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    
    def replace_image(match):
        alt_text = match.group(1)
        url = match.group(2)
        
        # Only process HTTP/HTTPS URLs
        if url.startswith(('http://', 'https://')):
            try:
                local_path = _download_image(url, temp_dir)
                downloaded_images.append(local_path)
                # Use filename only since base_url is set to temp_dir
                filename = local_path.name
                return f'![{alt_text}]({filename})'
            except Exception:
                # If download fails, keep original URL
                return match.group(0)
        else:
            # Keep local paths as is
            return match.group(0)
    
    processed_text = re.sub(image_pattern, replace_image, markdown_text)
    return processed_text, downloaded_images


def convert_markdown_text_to_pdf_file(
    markdown_text: str,
    output_pdf_path: Path,
) -> None:
    """Convert markdown text to PDF file using weasyprint with CSS stylesheets.
    
    After generating the base PDF with WeasyPrint, uses PyMuPDF to add:
    - Logo and link on first page
    - AIGC metadata
    - Page numbers
    """
    # Get project root directory (where fonts folder is located)
    project_root = Path(__file__).resolve().parent.parent.parent
    assets_dir = project_root / 'assets'
    
    # Load CSS stylesheets (cached for performance)
    styles, katex = _get_css_stylesheets(str(project_root), str(assets_dir))
    
    # Clean markdown text
    markdown_text = (markdown_text or '').strip('\ufeff')
    
    # Process blockquote code blocks first (before markdown conversion)
    markdown_text = _process_blockquote_code_blocks(markdown_text)
    
    # Process images: download external images to temporary directory
    with TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        processed_markdown, downloaded_images = _process_markdown_images(markdown_text, temp_dir)
        
        # Convert markdown to HTML (optimize extensions)
        # Note: HTML from _process_blockquote_code_blocks will pass through markdown parser
        html_content = markdown.markdown(
            processed_markdown,
            extensions=['extra', 'tables', 'fenced_code'],  # Removed codehilite for speed
        )
        
        # Wrap HTML content with proper structure (no header in CSS, will add via PyMuPDF)
        full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
</head>
<body>
{html_content}
</body>
</html>"""
        
        # Generate base PDF using weasyprint with optimized settings
        # Use temp_dir as base_url so WeasyPrint can find downloaded images
        html = HTML(string=full_html, base_url=str(temp_dir))
        pdf_stream: bytes = html.write_pdf(
            stylesheets=[styles, katex],
            optimize_images=True,
            presentational_hints=True,
        )
    
    # Open PDF with PyMuPDF to add logo, link, AIGC metadata, and page numbers
    with pymupdf.open(stream=pdf_stream) as doc:
        # Set AIGC file identifier metadata
        type_val, text = doc.xref_get_key(-1, 'Info')
        if type_val != 'xref':
            # Create Info dict if it doesn't exist
            doc.xref_set_key(-1, 'Info', f'{doc.xref_length()} 0 R')
            type_val, text = doc.xref_get_key(-1, 'Info')
        xref = int(text.replace('0 R', ''))
        doc.xref_set_key(
            xref=xref,
            key='AIGC',
            value=pymupdf.get_pdf_str(_generate_aigc_tag())
        )

        
        # Load logo images (Bohrium and MatMaster)        
        bohr_logo_img = None
        bohr_logo_stream = None
        matmaster_logo_img = None
        matmaster_logo_stream = None
        
        # Load Bohrium logo
        bohr_logo_path = assets_dir / 'Bohrium.png'
        with Image.open(bohr_logo_path) as img:
            bohr_logo_img = img.copy()
        with open(bohr_logo_path, 'rb') as f:
            bohr_logo_stream = f.read()

        # Load MatMaster logo
        matmaster_logo_path = assets_dir / 'MatMaster.png'
        with Image.open(matmaster_logo_path) as img:
            matmaster_logo_img = img.copy()
        with open(matmaster_logo_path, 'rb') as f:
            matmaster_logo_stream = f.read()
            
        # Logo size limits
        MAX_LOGO_WIDTH = 200
        MAX_LOGO_HEIGHT = 50
        
        # Calculate logo dimensions
        bohr_logo_w = bohr_logo_h = 0
        bohr_logo_resized_stream = None
        if bohr_logo_img:
            original_w = bohr_logo_img.width
            original_h = bohr_logo_img.height
            # Calculate target size (maintain aspect ratio)
            if original_w > MAX_LOGO_WIDTH or original_h > MAX_LOGO_HEIGHT:
                width_scale = MAX_LOGO_WIDTH / original_w
                height_scale = MAX_LOGO_HEIGHT / original_h
                scale = min(width_scale, height_scale)
                bohr_logo_w = int(original_w * scale)
                bohr_logo_h = int(original_h * scale)
            else:
                bohr_logo_w = original_w
                bohr_logo_h = original_h
            
            # Resize image to target size
            bohr_resized = bohr_logo_img.resize((bohr_logo_w, bohr_logo_h), Image.Resampling.LANCZOS)
            bohr_buffer = BytesIO()
            bohr_resized.save(bohr_buffer, format='PNG', optimize=True)
            bohr_logo_resized_stream = bohr_buffer.getvalue()
        
        matmaster_logo_w = matmaster_logo_h = 0
        matmaster_logo_resized_stream = None
        if matmaster_logo_img:
            original_w = matmaster_logo_img.width
            original_h = matmaster_logo_img.height
            # Calculate target size (maintain aspect ratio)
            if original_w > MAX_LOGO_WIDTH or original_h > MAX_LOGO_HEIGHT:
                width_scale = MAX_LOGO_WIDTH / original_w
                height_scale = MAX_LOGO_HEIGHT / original_h
                scale = min(width_scale, height_scale)
                matmaster_logo_w = int(original_w * scale)
                matmaster_logo_h = int(original_h * scale)
            else:
                matmaster_logo_w = original_w
                matmaster_logo_h = original_h
            
            # Resize image to target size
            matmaster_resized = matmaster_logo_img.resize((matmaster_logo_w, matmaster_logo_h), Image.Resampling.LANCZOS)
            matmaster_buffer = BytesIO()
            matmaster_resized.save(matmaster_buffer, format='PNG', optimize=True)
            matmaster_logo_resized_stream = matmaster_buffer.getvalue()
        
        # Process each page
        for page_id, page in enumerate(doc):
            h = page.rect.height
            w = page.rect.width
            
            # Add logos and links on first page only
            if page_id == 0 and bohr_logo_resized_stream and matmaster_logo_resized_stream:
                logo_start_y = 30
                left_start_x = 50  # Left margin
                content_right_margin = 54  # Corresponds to CSS margin-right: 1.91cm
                separator_width = 15  # Separator line width
                
                # Determine logo heights (use resized dimensions)
                # Display at 75% of resized size for better fit
                bohr_logo_height = bohr_logo_h * 0.75
                matmaster_logo_height = matmaster_logo_h * 0.75
                
                # Calculate widths based on resized dimensions, maintaining aspect ratio
                bohr_logo_width = (bohr_logo_w / bohr_logo_h) * bohr_logo_height
                matmaster_logo_width = (matmaster_logo_w / matmaster_logo_h) * matmaster_logo_height
                
                # Calculate total width for table column width ratio
                total_width = bohr_logo_width + separator_width + matmaster_logo_width
                
                # URL area height
                url_area_height = 20
                
                # Insert logos directly using insert_image (avoids base64 encoding overhead)
                # This is much more efficient and preserves image compression
                
                # Bohr logo
                bohr_logo_rect = pymupdf.Rect(
                    left_start_x,
                    logo_start_y,
                    left_start_x + bohr_logo_width,
                    logo_start_y + bohr_logo_height
                )
                page.insert_image(
                    rect=bohr_logo_rect,
                    stream=bohr_logo_resized_stream,  # Use resized image
                    keep_proportion=True,
                )
                
                # Separator line (draw directly)
                separator_x = left_start_x + bohr_logo_width + separator_width / 2
                shape = page.new_shape()
                shape.draw_line(
                    pymupdf.Point(separator_x, logo_start_y),
                    pymupdf.Point(separator_x, logo_start_y + bohr_logo_height),
                )
                shape.finish(color=(0.5, 0.5, 0.5), width=1)
                shape.commit()
                
                # MatMaster logo (centered vertically with Bohr logo)
                matmaster_actual_height = matmaster_logo_height * 0.6
                matmaster_y_offset = (bohr_logo_height - matmaster_actual_height) / 2
                matmaster_logo_rect = pymupdf.Rect(
                    left_start_x + bohr_logo_width + separator_width,
                    logo_start_y + matmaster_y_offset,
                    left_start_x + bohr_logo_width + separator_width + matmaster_logo_width * 0.6,
                    logo_start_y + matmaster_y_offset + matmaster_actual_height
                )
                page.insert_image(
                    rect=matmaster_logo_rect,
                    stream=matmaster_logo_resized_stream,  # Use resized image
                    keep_proportion=True,
                )
                
                # Insert links using HTML (text only, no images - much smaller)
                links_html = f'''
                <table style="width: 100%; border: none; padding: 0; margin: 0; font-size: 8px; border-collapse: collapse;">
                    <tr>
                        <td style="width: {bohr_logo_width/total_width*100:.2f}%; text-align: center; padding: 0; margin: 0; vertical-align: top; padding-top: 0;">
                            <div style="text-align: center;">
                                <a href="https://www.bohrium.com" style="font-size: 8px;">www.bohrium.com</a>
                            </div>
                        </td>
                        <td style="width: {separator_width/total_width*100:.2f}%; text-align: center; padding: 0; margin: 0; vertical-align: top;"></td>
                        <td style="width: {matmaster_logo_width/total_width*100:.2f}%; text-align: center; padding: 0; margin: 0; vertical-align: top; padding-top: 0;">
                            <div style="text-align: center;">
                                <a href="https://matmaster.bohrium.com/matmaster" style="font-size: 8px;">https://matmaster.bohrium.com/matmaster</a>
                            </div>
                        </td>
                    </tr>
                </table>
                '''
                
                # Links rectangle (below logos)
                links_rect = pymupdf.Rect(
                    left_start_x,
                    logo_start_y + bohr_logo_height,
                    left_start_x + total_width,
                    logo_start_y + bohr_logo_height + url_area_height
                )
                
                # Insert links HTML (text only, no images)
                page.insert_htmlbox(
                    rect=links_rect,
                    text=links_html,
                )
                
                # Add link annotations separately to ensure they're clickable
                # Bohr link
                bohr_link_rect = pymupdf.Rect(
                    left_start_x,
                    logo_start_y + bohr_logo_height,
                    left_start_x + bohr_logo_width,
                    logo_start_y + bohr_logo_height + url_area_height
                )
                page.insert_link({
                    "kind": pymupdf.LINK_URI,
                    "from": bohr_link_rect,
                    "uri": "https://www.bohrium.com",
                })
                
                # MatMaster link
                matmaster_link_rect = pymupdf.Rect(
                    left_start_x + bohr_logo_width + separator_width,
                    logo_start_y + bohr_logo_height,
                    left_start_x + total_width,
                    logo_start_y + bohr_logo_height + url_area_height
                )
                page.insert_link({
                    "kind": pymupdf.LINK_URI,
                    "from": matmaster_link_rect,
                    "uri": "https://matmaster.bohrium.com/matmaster",
                })
                
                # Right side: "AI Generated" and "Generated by MatMaster"
                right_area_width = 150
                right_area_start_x = w - right_area_width - content_right_margin
                content_right_edge = w - content_right_margin
                right_area_start_y = logo_start_y
                
                # Insert text using textbox (faster than HTML)
                right_text_rect = pymupdf.Rect(
                    right_area_start_x,
                    right_area_start_y,
                    content_right_edge,
                    right_area_start_y + 30
                )
                page.insert_textbox(
                    rect=right_text_rect,
                    buffer="AI Generated\nGenerated by MatMaster",
                    fontsize=9,
                    color=(0.3, 0.3, 0.3),  # Gray color
                    align=pymupdf.TEXT_ALIGN_RIGHT,
                )
            
            # Add page number (all pages)
            page.insert_textbox(
                rect=pymupdf.Rect(0, h - 40, w, h - 20),
                buffer=f'{page_id + 1}',
                align=pymupdf.TEXT_ALIGN_CENTER,
            )
        
        # Save the modified PDF with compression
        # Use garbage collection and deflate compression to reduce file size
        doc.save(
            output_pdf_path,
            garbage=4,  # Remove unused objects
            deflate=True,  # Compress streams
            clean=True,  # Clean and sanitize content streams
        )
