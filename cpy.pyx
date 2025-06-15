# cython: language_level=3
# cython: boundscheck=False, wraparound=False, nonecheck=False

import numpy as np
cimport numpy as np
cimport cython

from libc.math cimport pow
#import time
from PIL import Image
from libc.stdint cimport uint16_t, uint32_t

# Constants
EPD_W = 800
EPD_H = 480

ctypedef np.float32_t FLOAT_TYPE
ctypedef np.uint8_t UINT8_TYPE

cdef uint32_t sqDiff(uint16_t a, uint16_t b) nogil:
    return (a - b) * (a - b)

#---------------------
#Original Floyd-Steinberg
cdef int closestColor(uint16_t r, uint16_t g, uint16_t b, double[:, :] linearPalette) nogil:
    """
    找出最接近的顏色索引，使用加權的平方距離計算
    """
    cdef int color = 0
    cdef uint32_t best = 2**32 - 1  # MaxUint32
    cdef uint32_t dist
    cdef int i, j
    cdef double rr, gg, bb

    for i in range(linearPalette.shape[0]):
        rr = linearPalette[i, 0]
        gg = linearPalette[i, 1]
        bb = linearPalette[i, 2]
        
        # 使用加權的平方距離計算
        dist = (
            (<uint32_t>(1063 * sqDiff(r, <uint16_t>(rr * 255)) / 5000)) +
            (<uint32_t>(447 * sqDiff(g, <uint16_t>(gg * 255)) / 625)) +
            (<uint32_t>(361 * sqDiff(b, <uint16_t>(bb * 255)) / 5000))
        )
        
        # find the closest colors
        if dist < best:
            if dist == 0:
                return i
            color = i
            best = dist
    
    return color

cdef double gamma_linear(double inp) nogil:
    """Convert sRGB to linear RGB."""
    if inp > 0.04045:
        return pow((inp + 0.055) / (1.0 + 0.055), 2.4)
    return inp / 12.92

def load_scaled(image, angle, display_mode='fit'):
    if isinstance(image, str):
        img = Image.open(image)
    else:
        img = image.copy()

    img = img.convert('RGB')
    img = img.rotate(angle, expand=True)
    
    # 根據顯示模式調整圖像大小
    if display_mode == 'fill':
        # 填滿螢幕模式：將圖像裁剪並縮放以填滿整個螢幕
        orig_ratio = img.width / img.height
        epd_ratio = EPD_W / EPD_H
        
        if orig_ratio > epd_ratio:
            # 圖像太寬，需要裁剪兩側
            new_height = EPD_H
            new_width = int(new_height * orig_ratio)
            img = img.resize((new_width, new_height), Image.LANCZOS)
            left = (new_width - EPD_W) // 2
            img = img.crop((left, 0, left + EPD_W, EPD_H))
        else:
            # 圖像太高，需要裁剪上下
            new_width = EPD_W
            new_height = int(new_width / orig_ratio)
            img = img.resize((new_width, new_height), Image.LANCZOS)
            top = (new_height - EPD_H) // 2
            img = img.crop((0, top, EPD_W, top + EPD_H))
    else:
        # 原有的符合螢幕寬度模式
        orig_ratio = img.width / img.height
        epd_ratio = EPD_W / EPD_H
        
        if orig_ratio > epd_ratio:
            new_width = EPD_W
            new_height = int(new_width / orig_ratio)
        else:
            new_height = EPD_H
            new_width = int(new_height * orig_ratio)
        
        img = img.resize((new_width, new_height), Image.LANCZOS)
        
        bg = Image.new('RGB', (EPD_W, EPD_H), (255, 255, 255))
        offset = ((EPD_W - new_width) // 2, (EPD_H - new_height) // 2)
        bg.paste(img, offset)
        return bg
    
    return img

def convert_image(input_image, preview_path=None, dithering_strength=1.0):
    """Cython-optimized image conversion function."""
    # Prepare input data
    cdef np.ndarray[np.uint8_t, ndim=3] img_array = np.array(input_image, dtype=np.uint8)
    
    cdef double[:, :] epd_colors = np.array([
        [0, 0, 0],
        [1, 1, 1],
        [1, 1, 0],
        [1, 0, 0],
        [0, 0, 1],
        [0, 1, 0],
    ], dtype=np.float64)
    
    # Prepare output arrays
    cdef np.ndarray[np.uint8_t, ndim=3] pixels = np.zeros((EPD_H, EPD_W, 3), dtype=np.uint8)
    cdef np.ndarray[np.uint8_t, ndim=3] output_img = np.zeros((EPD_H, EPD_W, 3), dtype=np.uint8)

    # Copy and convert input image
    cdef int x, y, c, best, ob
    cdef double diff, min_diff
    
    for y in range(EPD_H):
        for x in range(EPD_W):
            # Convert pixel
            #for c in range(3):
            #    pixels[y, x, c] = <np.uint8_t>(gamma_linear(img_array[y, x, c] / 255.0) * 255)
            for c in range(3):
                pixels[y, x, c] = img_array[y, x, c]

    # Color quantization with Floyd-Steinberg dithering
    ob = 0
    for y in range(EPD_H):
        for x in range(EPD_W):
            # Find closest EPD color
            min_diff = 1e10
            best = 0
            for c in range(epd_colors.shape[0]):
                diff = 0
                for i in range(3):
                    diff += (pixels[y, x, i] / 255.0 - epd_colors[c, i]) ** 2
                if diff < min_diff:
                    min_diff = diff
                    best = c

    # Color quantization with Floyd-Steinberg dithering and improved color matching
    #for y in range(EPD_H):
    #    for x in range(EPD_W):
    #        # Find closest EPD color using weighted distance
    #        best = closestColor(
    #            <uint16_t>pixels[y, x, 0], 
    #            <uint16_t>pixels[y, x, 1], 
    #            <uint16_t>pixels[y, x, 2], 
    #            epd_colors
    #        )

            # Floyd-Steinberg error distribution with strength control
            dithering_strength = dithering_strength  # New parameter to control error diffusion (0.0 to 1.0)

            for c in range(3):
                diff = (pixels[y, x, c] / 255.0 - epd_colors[best, c])
                
                # Scale the diff by dithering_strength
                scaled_diff = diff * dithering_strength
                
                # Right pixel
                if x+1 < EPD_W:
                    pixels[y, x+1, c] = <np.uint8_t>(min(max(pixels[y, x+1, c] + <int>(scaled_diff * 7/16 * 255), 0), 255))
                
                # Bottom-left pixel
                if x-1 >= 0 and y+1 < EPD_H:
                    pixels[y+1, x-1, c] = <np.uint8_t>(min(max(pixels[y+1, x-1, c] + <int>(scaled_diff * 3/16 * 255), 0), 255))
                
                # Bottom pixel
                if y+1 < EPD_H:
                    pixels[y+1, x, c] = <np.uint8_t>(min(max(pixels[y+1, x, c] + <int>(scaled_diff * 5/16 * 255), 0), 255))
                
                # Bottom-right pixel
                if x+1 < EPD_W and y+1 < EPD_H:
                    pixels[y+1, x+1, c] = <np.uint8_t>(min(max(pixels[y+1, x+1, c] + <int>(scaled_diff * 1/16 * 255), 0), 255))

            # Set output image pixel
            for c in range(3):
                output_img[y, x, c] = <np.uint8_t>(epd_colors[best, c] * 255)

    # Optional file outputs

    return output_img

#---------------------
#End Original Floyd-Steinberg

#---------------------
#Atkinson

cdef int closestColor_Atkinson(int r, int g, int b, double[:, :] epd_colors) nogil:
    """Finde die nächste Farbe in der EPD-Palette mit gewichteter Distanz."""
    cdef int color = 0
    cdef double best = 1e10
    cdef double dist
    cdef int i
    cdef double rr, gg, bb
    
    for i in range(epd_colors.shape[0]):
        rr = epd_colors[i, 0]
        gg = epd_colors[i, 1] 
        bb = epd_colors[i, 2]
        
        # Gewichtete euklidische Distanz optimiert für menschliche Wahrnehmung
        dist = (
            ((1063 * sqDiff_Atkinson(r, (rr * 255)) / 5000)) +
            ((447 * sqDiff_Atkinson(g, (gg * 255)) / 625)) +
            ((361 * sqDiff_Atkinson(b, (bb * 255)) / 5000))
        )
        
        if dist < best:
            if dist == 0:
                return i
            color = i
            best = dist
            
    return color

cdef double sqDiff_Atkinson(double a, double b) nogil:
    """Berechne quadratische Differenz."""
    return (a - b) * (a - b)

#cdef double gamma_linear(double inp) nogil:
#    """Konvertiere sRGB zu linearem RGB."""
#    if inp > 0.04045:
#        return pow((inp + 0.055) / (1.0 + 0.055), 2.4)
#    return inp / 12.92

def convert_image_atkinson(input_image, preview_path=None, dithering_strength=1.0):
    """Cython-optimierte Bildkonvertierung mit Atkinson Dithering."""
    # Eingabedaten vorbereiten
    cdef np.ndarray[np.uint8_t, ndim=3] img_array = np.array(input_image, dtype=np.uint8)
    
    cdef double[:, :] epd_colors = np.array([
        [0, 0, 0],    # Schwarz
        [1, 1, 1],    # Weiß  
        [1, 1, 0],    # Gelb
        [1, 0, 0],    # Rot
        [0, 0, 1],    # Blau
        [0, 1, 0],    # Grün
    ], dtype=np.float64)
    
    # Ausgabe-Arrays vorbereiten
    cdef np.ndarray[np.uint8_t, ndim=3] pixels = np.zeros((EPD_H, EPD_W, 3), dtype=np.uint8)
    cdef np.ndarray[np.uint8_t, ndim=3] output_img = np.zeros((EPD_H, EPD_W, 3), dtype=np.uint8)

    # Lokale Variablen mit expliziten Typen
    cdef int x, y, c, best, i
    cdef double diff, min_diff, scaled_diff, error_value
    
    # Eingabebild kopieren
    for y in range(EPD_H):
        for x in range(EPD_W):
            for c in range(3):
                pixels[y, x, c] = img_array[y, x, c]

    # Farbquantisierung mit Atkinson Dithering
    for y in range(EPD_H):
        for x in range(EPD_W):
            # Finde nächste EPD-Farbe
            min_diff = 1e10
            best = 0
            for c in range(epd_colors.shape[0]):
                diff = 0.0
                for i in range(3):
                    diff += (pixels[y, x, i] / 255.0 - epd_colors[c, i]) ** 2
                if diff < min_diff:
                    min_diff = diff
                    best = c

            # Atkinson Fehlerverteilung mit expliziten Typ-Casts
            for c in range(3):
                diff = (pixels[y, x, c] / 255.0 - epd_colors[best, c])
                scaled_diff = diff * dithering_strength * 0.75
                
                # Atkinson Dithering-Muster mit korrekten Typ-Casts:
                
                # Rechter Pixel (x+1, y)
                if x+1 < EPD_W:
                    error_value = scaled_diff * (1.0/8.0) * 255.0
                    pixels[y, x+1, c] = <np.uint8_t>min(max(<double>pixels[y, x+1, c] + error_value, 0.0), 255.0)
                
                # Zwei Pixel rechts (x+2, y)
                if x+2 < EPD_W:
                    error_value = scaled_diff * (1.0/8.0) * 255.0
                    pixels[y, x+2, c] = <np.uint8_t>min(max(<double>pixels[y, x+2, c] + error_value, 0.0), 255.0)
                
                # Unten-links Pixel (x-1, y+1)
                if x-1 >= 0 and y+1 < EPD_H:
                    error_value = scaled_diff * (1.0/8.0) * 255.0
                    pixels[y+1, x-1, c] = <np.uint8_t>min(max(<double>pixels[y+1, x-1, c] + error_value, 0.0), 255.0)
                
                # Unterer Pixel (x, y+1)
                if y+1 < EPD_H:
                    error_value = scaled_diff * (1.0/8.0) * 255.0
                    pixels[y+1, x, c] = <np.uint8_t>min(max(<double>pixels[y+1, x, c] + error_value, 0.0), 255.0)
                
                # Unten-rechts Pixel (x+1, y+1)
                if x+1 < EPD_W and y+1 < EPD_H:
                    error_value = scaled_diff * (1.0/8.0) * 255.0
                    pixels[y+1, x+1, c] = <np.uint8_t>min(max(<double>pixels[y+1, x+1, c] + error_value, 0.0), 255.0)
                
                # Zwei Zeilen nach unten, mittlerer Pixel (x, y+2)
                if y+2 < EPD_H:
                    error_value = scaled_diff * (1.0/8.0) * 255.0
                    pixels[y+2, x, c] = <np.uint8_t>min(max(<double>pixels[y+2, x, c] + error_value, 0.0), 255.0)

            # Setze Ausgabebild-Pixel mit explizitem Cast - KRITISCHE KORREKTUR
            for c in range(3):
                output_img[y, x, c] = <np.uint8_t>(epd_colors[best, c] * 255.0)

    return output_img

#---------------------
#End Atkinson