import json
import math
import os
from PIL import Image, ImageDraw

# --- Configuration --- (paths are relative to this script's folder)
HERE = os.path.dirname(os.path.abspath(__file__))
BDF_PATH = os.path.join(HERE, "cozette_hidpi.bdf")
OUTPUT_IMG = os.path.join(HERE, "textures", "billboards_nearest", "cozette_hidpi_atlas.png")  # loaded by TextureManager
OUTPUT_META = os.path.join(HERE, "cozette_hidpi.json")
ATLAS_SIZE = 512

# Define the ranges you want (Integers)
# You can import these from your font_defs.py if you created it
RANGES = [
    range(32, 127),        # ASCII
    range(0x0400, 0x0500), # Cyrillic
    # Add your specific icon codes here, e.g.:
    # [0x2665, 0xE0A0] 
]

def parse_bdf(filepath):
    """
    Parses a BDF file and returns a dict: { unicode_int: {'w': w, 'h': h, 'bitmap': [rows]} }
    """
    glyphs = {}
    current_char = None
    in_bitmap = False
    
    with open(filepath, 'rb') as f: # Binary mode to handle potential encoding weirdness
        lines = f.readlines()
        
    for line in lines:
        line = line.decode('utf-8', errors='ignore').strip()
        
        if line.startswith("ENCODING"):
            # Format: ENCODING 65
            code = int(line.split()[1])
            current_char = code
            glyphs[code] = {'w': 0, 'h': 0, 'ox': 0, 'oy': 0, 'advance': 0, 'bitmap': []}

        elif line.startswith("DWIDTH"):
            # Format: DWIDTH advance_x advance_y
            if current_char is not None:
                glyphs[current_char]['advance'] = int(line.split()[1])

        elif line.startswith("BBX"):
            # Format: BBX width height x_offset y_offset
            # Offsets place the bitmap's bottom-left corner relative to the
            # pen position on the baseline (y up), needed for descenders.
            parts = [int(x) for x in line.split()[1:]]
            if current_char is not None:
                glyphs[current_char]['w'] = parts[0]
                glyphs[current_char]['h'] = parts[1]
                glyphs[current_char]['ox'] = parts[2]
                glyphs[current_char]['oy'] = parts[3]
                
        elif line.startswith("BITMAP"):
            in_bitmap = True
            
        elif line.startswith("ENDCHAR"):
            in_bitmap = False
            current_char = None
            
        elif in_bitmap and current_char is not None:
            # Append hex string row
            glyphs[current_char]['bitmap'].append(line)
            
    return glyphs

def bake():
    print(f"Parsing {BDF_PATH}...")
    bdf_glyphs = parse_bdf(BDF_PATH)
    
    # Filter only the chars we want
    wanted_codes = set()
    for r in RANGES:
        wanted_codes.update(r)
    
    # Create sorted list of chars that actually exist in the font
    final_chars = sorted([c for c in wanted_codes if c in bdf_glyphs])
    print(f"Found {len(final_chars)} valid glyphs out of {len(wanted_codes)} requested.")

    # Determine Cell Size (Monospace assumption helps, but we can handle variable)
    # Cozette is usually 6px wide, 13px tall bounding box.
    # Let's find the max dimensions to be safe.
    max_w = max(bdf_glyphs[c]['w'] for c in final_chars)
    max_h = max(bdf_glyphs[c]['h'] for c in final_chars)
    
    # We add 1px padding just in case to avoid bleeding
    cell_w, cell_h = max_w, max_h 
    
    cols = ATLAS_SIZE // cell_w
    rows = ATLAS_SIZE // cell_h
    
    if len(final_chars) > cols * rows:
        raise Exception("Atlas too small! Increase ATLAS_SIZE.")

    # Create Image
    img = Image.new('RGBA', (ATLAS_SIZE, ATLAS_SIZE), (0, 0, 0, 0))
    pixels = img.load() # Direct pixel access is faster for bits
    
    mapping = {}

    for idx, code in enumerate(final_chars):
        glyph = bdf_glyphs[code]
        
        # Grid position
        col_idx = idx % cols
        row_idx = idx // cols
        base_x = col_idx * cell_w
        base_y = row_idx * cell_h
        
        # Draw Bitmap
        # BDF bitmaps are hex strings, row by row.
        # Example: "FC" -> 11111100
        bitmap = glyph['bitmap']
        g_w = glyph['w']
        g_h = glyph['h']
        
        # Center the glyph in the cell (optional) or top-left align
        # Standard Cozette is usually top-aligned or baseline aligned. 
        # For simplicity, we align top-left here.
        
        for y, hex_row in enumerate(bitmap):
            # Convert hex to integer
            row_int = int(hex_row, 16)
            # Iterate bits. BDF is MSB first (most significant bit is left-most pixel)
            # The row might have padding bits if width is not multiple of 8
            # but we only care about the first g_w bits.
            
            # The bit width in the file is rounded up to nearest byte (8 bits)
            # e.g. width 6 -> uses 8 bits. width 9 -> uses 16 bits.
            
            # How many bits are actually in this row data?
            # It's always hex_len * 4.
            
            for x in range(g_w):
                # Check bit at position x (from left)
                # To check bit 0 (leftmost), we look at bit (total_bits - 1) of the int
                # But simpler: mask = 1 << (row_width_in_bits - 1 - x)
                
                # BDF standard: row is padded to byte boundary.
                # E.g. width 6: 0xFC (11111100). Bit 0 is 1. Bit 5 is 1. Bit 6,7 ignored.
                # We need to calculate the bit shift.
                
                row_bits_width = len(hex_row) * 4
                mask = 1 << (row_bits_width - 1 - x)
                
                if row_int & mask:
                    pixels[base_x + x, base_y + y] = (255, 255, 255, 255)

        # Store Metadata
        # Normalize UVs
        u_min = base_x / ATLAS_SIZE
        v_min = base_y / ATLAS_SIZE
        u_max = (base_x + g_w) / ATLAS_SIZE # Tightly packed width
        v_max = (base_y + g_h) / ATLAS_SIZE # Tightly packed height
        
        mapping[code] = {
            "uv": [u_min, v_min, u_max, v_max],
            "size": [g_w, g_h],
            "offset": [glyph['ox'], glyph['oy']],
            "advance": glyph['advance'],
        }

    img.save(OUTPUT_IMG)
    with open(OUTPUT_META, 'w') as f:
        json.dump(mapping, f, separators=(',', ':')) # Minified JSON
    
    print(f"Success! Saved {OUTPUT_IMG} and {OUTPUT_META}")

if __name__ == "__main__":
    bake()