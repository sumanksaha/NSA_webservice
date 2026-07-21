from PIL import Image, ImageDraw, ImageFont
import os
from datetime import datetime


def process_and_stamp_image(image_file, locality: str, captured_at: str, verification_status: str, image_id: str, case_id: str) -> str:
    """
    Takes an uploaded image file object, processes it, and saves to disk.
    Returns the final filepath (str).
    """
    # Open the image
    img = Image.open(image_file)

    # Resize if longer edge > 1600px
    max_size = 1600
    if max(img.size) > max_size:
        ratio = max_size / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    # Strip EXIF data
    if hasattr(img, '_getexif'):
        img.info.pop('exif', None)

    # Create a drawing context
    draw = ImageDraw.Draw(img)
    img_width, img_height = img.size

    # Draw semi-transparent dark banner at the bottom
    banner_height = int(img_height * 0.15)
    banner_color = (0, 0, 0, 180)  # Semi-transparent black
    draw.rectangle(
        [(0, img_height - banner_height), (img_width, img_height)],
        fill=banner_color
    )

    # Prepare text for the stamp
    text_lines = []
    if verification_status == "FLAG":
        text_lines.append("⚠ UNVERIFIED — Manual Review Required")
    else:
        text_lines.append(locality if locality else "Unknown Location")
        text_lines.append(captured_at)
        text_lines.append(f"Status: {verification_status}")

    # Use default font (TODO: replace with a better font if available in static assets)
    try:
        font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()

    # Calculate text position and draw
    text_y = img_height - banner_height + 10
    for line in text_lines:
        draw.text((10, text_y), line, fill=(255, 255, 255, 255), font=font)
        text_y += 20  # Move to the next line

    # Parse captured_at to get YYYY and MM
    try:
        captured_datetime = datetime.fromisoformat(captured_at)
        year = captured_datetime.strftime("%Y")
        month = captured_datetime.strftime("%m")
    except:
        year = "unknown"
        month = "unknown"

    # Create the directory path
    output_dir = os.path.join("photos", year, month, case_id)
    os.makedirs(output_dir, exist_ok=True)

    # Save as WebP
    output_path = os.path.join(output_dir, f"{image_id}.webp")
    img.save(output_path, format="WEBP", quality=78)

    return output_path