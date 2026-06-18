#!/usr/bin/env python3
import subprocess
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"

slides = [
    {
        "input": "ops/foxtail_coffee_interior.png",
        "title": "YOUR DAILY ESCAPE",
        "subtitle": "Welcome to Foxtail Coffee Co.",
        "output": "ops/slide1.mp4",
        "temp_img": "ops/temp_slide1.png"
    },
    {
        "input": "ops/barista_pour.png",
        "title": "CRAFTED WITH PASSION",
        "subtitle": "Every cup is a work of art.",
        "output": "ops/slide2.mp4",
        "temp_img": "ops/temp_slide2.png"
    },
    {
        "input": "ops/croissant_and_coffee.png",
        "title": "FIND YOUR FOCUS",
        "subtitle": "The perfect corner for work or study.",
        "output": "ops/slide3.mp4",
        "temp_img": "ops/temp_slide3.png"
    },
    {
        "input": "ops/cafe_exterior.png",
        "title": "LOCALLY OWNED, LOCALLY LOVED",
        "subtitle": "Join us for your next cup today.",
        "output": "ops/slide4.mp4",
        "temp_img": "ops/temp_slide4.png"
    }
]

def add_text_overlay(inp_path, out_path, title, subtitle):
    # Open image
    img = Image.open(inp_path).convert("RGBA")
    w, h = img.size
    
    # Create overlay for transparent rounded rectangle
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    
    # Rounded rectangle coordinates (centered at the bottom)
    rect_x0 = 50
    rect_y0 = h - 250
    rect_x1 = w - 50
    rect_y1 = h - 70
    draw_overlay.rounded_rectangle([rect_x0, rect_y0, rect_x1, rect_y1], radius=15, fill=(0, 0, 0, 160))
    
    # Composite the overlay onto the original image
    img = Image.alpha_composite(img, overlay)
    
    # Load fonts
    font_title = ImageFont.truetype(FONT_PATH, 48)
    font_subtitle = ImageFont.truetype(FONT_PATH, 28)
    
    # Draw text onto composite image
    draw = ImageDraw.Draw(img)
    
    # Title (White)
    title_w = draw.textlength(title, font=font_title)
    title_x = (w - title_w) // 2
    draw.text((title_x, h - 215), title, font=font_title, fill=(255, 255, 255, 255))
    
    # Subtitle (Gold)
    sub_w = draw.textlength(subtitle, font=font_subtitle)
    sub_x = (w - sub_w) // 2
    draw.text((sub_x, h - 135), subtitle, font=font_subtitle, fill=(255, 215, 0, 255))
    
    # Save as RGB PNG
    img.convert("RGB").save(out_path, "PNG")

def render_slide(slide):
    inp = slide["input"]
    temp_img = slide["temp_img"]
    out = slide["output"]
    title = slide["title"]
    subtitle = slide["subtitle"]
    
    # Add text overlay to image first
    add_text_overlay(inp, temp_img, title, subtitle)
    
    # Run FFmpeg to zoom the text-overlayed image (225 frames = 7.5s at 30 fps)
    vf = "zoompan=z='zoom+0.0004':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=225:s=1080x1080"
    
    cmd = [
        "ffmpeg", "-loop", "1", "-i", temp_img,
        "-vf", vf,
        "-c:v", "libx264", "-t", "7.5", "-r", "30",
        "-pix_fmt", "yuv420p", "-y", out
    ]
    
    print(f"Rendering {inp} -> {out}...")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    # Render intermediate slides
    for s in slides:
        render_slide(s)
        
    # Create concat list
    concat_file = ROOT / "ops" / "concat.txt"
    with concat_file.open("w") as f:
        for s in slides:
            f.write(f"file '{Path(s['output']).name}'\n")
            
    # Concatenate slides
    final_output = ROOT / "ops" / "foxtail_30s_reel.mp4"
    concat_cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy", "-y", str(final_output)
    ]
    
    print("Concatenating slides into final 30s video...")
    subprocess.run(concat_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Clean up intermediate files
    print("Cleaning up intermediate files...")
    concat_file.unlink()
    for s in slides:
        try:
            Path(s["output"]).unlink()
            Path(s["temp_img"]).unlink()
        except OSError:
            pass
            
    print(f"Success! 30-second user-engaging video rendered at {final_output}")

if __name__ == "__main__":
    main()
