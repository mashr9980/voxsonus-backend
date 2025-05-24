# app/services/subtitle_processor.py
import asyncio
import asyncpg
import os
import json
import logging
import requests
from datetime import datetime
from typing import List, Dict, Any
from app.core.config import settings
from app.core.utils import delete_file, create_output_directory
from app.models.order import OrderStatus, VideoStatus, OutputFormat

logger = logging.getLogger(__name__)

async def process_order(order_id: int):
    """Process a paid order by generating subtitles for all videos"""
    conn = None
    try:
        # Connect to database
        conn = await asyncpg.connect(settings.DATABASE_URL)
        
        # Update order status
        await conn.execute(
            "UPDATE orders SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            OrderStatus.PROCESSING, order_id
        )
        
        # Get order details
        order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        user_id = order["user_id"]
        
        # Get subtitle config
        subtitle_config = await conn.fetchrow(
            "SELECT * FROM subtitle_configs WHERE order_id = $1", order_id
        )
        
        # Get videos
        videos = await conn.fetch(
            "SELECT * FROM videos WHERE order_id = $1", order_id
        )
        
        # Create output directory
        output_dir = create_output_directory(user_id, order_id)
        
        # Process each video
        for video in videos:
            try:
                # Update video status
                await conn.execute(
                    "UPDATE videos SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                    VideoStatus.PROCESSING, video["id"]
                )
                
                # Process video
                subtitle_files = await generate_subtitles(
                    video, subtitle_config, output_dir, conn
                )
                
                # Update video status
                await conn.execute(
                    "UPDATE videos SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                    VideoStatus.COMPLETED, video["id"]
                )
                
                # Delete source video file after processing
                await delete_file(video["file_path"])
            except Exception as e:
                logger.error(f"Error processing video {video['id']} for order {order_id}: {e}")
                
                # Update video status to failed
                await conn.execute(
                    "UPDATE videos SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                    VideoStatus.FAILED, video["id"]
                )
        
        # Check if all videos are processed
        all_videos_processed = True
        videos_status = await conn.fetch(
            "SELECT status FROM videos WHERE order_id = $1", order_id
        )
        
        for video_status in videos_status:
            if video_status["status"] not in [VideoStatus.COMPLETED, VideoStatus.FAILED]:
                all_videos_processed = False
                break
        
        # Update order status
        final_status = OrderStatus.COMPLETED if all_videos_processed else OrderStatus.FAILED
        await conn.execute(
            "UPDATE orders SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            final_status, order_id
        )
    except Exception as e:
        logger.error(f"Error processing order {order_id}: {e}")
        
        # Update order status to failed
        if conn:
            await conn.execute(
                "UPDATE orders SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                OrderStatus.FAILED, order_id
            )
    finally:
        # Close database connection
        if conn:
            await conn.close()

async def generate_subtitles(
    video: Dict[str, Any],
    config: Dict[str, Any],
    output_dir: str,
    conn: asyncpg.Connection
) -> List[str]:
    """Generate subtitle files for a video using AI services"""
    try:
        # Generate subtitles with AssemblyAI
        speech_subtitles = await generate_speech_subtitles(
            video["file_path"], config["source_language"]
        )
        
        # Generate non-verbal sound subtitles with YAMNet
        sound_subtitles = await generate_sound_subtitles(
            video["file_path"], config["genre"]
        )
        
        # Merge both subtitle types, filter and normalize as needed
        merged_subtitles = merge_subtitles(
            speech_subtitles, 
            sound_subtitles, 
            config["accessibility_mode"],
            config["non_verbal_only_mode"]
        )
        
        # Format according to user preferences
        formatted_subtitles = format_subtitles(
            merged_subtitles,
            config["max_chars_per_line"],
            config["lines_per_subtitle"]
        )
        
        # Translate non-verbal labels if needed
        if config["target_language"] and config["target_language"] != config["source_language"]:
            translated_subtitles = await translate_subtitles(
                formatted_subtitles,
                config["source_language"],
                config["target_language"]
            )
        else:
            translated_subtitles = formatted_subtitles
        
        # Export to requested format(s)
        subtitle_files = []
        output_format = config["output_format"]
        
        # Determine output filename base
        filename_base = f"{os.path.splitext(video['original_filename'])[0]}"
        
        # Export to the requested format
        output_file = export_subtitles(
            translated_subtitles,
            output_dir,
            filename_base,
            output_format
        )
        subtitle_files.append(output_file)
        
        # Save subtitle files to database
        for file_path in subtitle_files:
            await conn.execute("""
                INSERT INTO subtitle_files (video_id, config_id, file_path, file_format)
                VALUES ($1, $2, $3, $4)
            """, video["id"], config["id"], file_path, os.path.splitext(file_path)[1][1:])
        
        return subtitle_files
    except Exception as e:
        logger.error(f"Error generating subtitles: {e}")
        raise

async def generate_speech_subtitles(file_path: str, language: str) -> List[Dict]:
    """
    Generate speech subtitles using AssemblyAI API
    This is a placeholder implementation - in production you would implement the actual API call
    """
    try:
        # Placeholder implementation
        # In a real implementation, you would:
        # 1. Upload the file to AssemblyAI
        # 2. Start transcription job
        # 3. Poll for completion
        # 4. Get results and format as subtitles
        
        # Simulated result
        return [
            {"start": 0, "end": 5000, "text": "This is sample speech text.", "type": "speech"},
            {"start": 6000, "end": 10000, "text": "More sample text here.", "type": "speech"},
        ]
    except Exception as e:
        logger.error(f"Error generating speech subtitles: {e}")
        raise

async def generate_sound_subtitles(file_path: str, genre: str) -> List[Dict]:
    """
    Generate non-verbal sound subtitles using YAMNet
    This is a placeholder implementation - in production you would implement the actual model usage
    """
    try:
        # Placeholder implementation
        # In a real implementation, you would:
        # 1. Extract audio from video
        # 2. Run YAMNet model on audio segments
        # 3. Identify sound events
        # 4. Format as subtitles
        
        # Simulated result
        return [
            {"start": 2500, "end": 3500, "text": "Door slamming", "type": "sound"},
            {"start": 8000, "end": 9000, "text": "Footsteps", "type": "sound"},
        ]
    except Exception as e:
        logger.error(f"Error generating sound subtitles: {e}")
        raise

def merge_subtitles(
    speech_subtitles: List[Dict],
    sound_subtitles: List[Dict],
    accessibility_mode: bool,
    non_verbal_only_mode: bool
) -> List[Dict]:
    """Merge speech and sound subtitles according to user preferences"""
    try:
        merged = []
        
        # If non-verbal only mode is enabled, only include sound subtitles
        if non_verbal_only_mode:
            for sub in sound_subtitles:
                # Format non-verbal sounds with brackets
                sub["text"] = f"[{sub['text']}]"
                merged.append(sub)
            return sorted(merged, key=lambda x: x["start"])
        
        # Otherwise, include both speech and sound subtitles
        merged = speech_subtitles.copy()
        
        for sound_sub in sound_subtitles:
            # Format non-verbal sounds with brackets
            sound_sub["text"] = f"[{sound_sub['text']}]"
            
            # If accessibility mode is enabled, add all sound subtitles
            if accessibility_mode:
                merged.append(sound_sub)
            else:
                # Otherwise, only add sounds that don't overlap with speech
                is_overlapping = False
                for speech_sub in speech_subtitles:
                    # Check for overlap
                    if (sound_sub["start"] <= speech_sub["end"] and 
                        sound_sub["end"] >= speech_sub["start"]):
                        is_overlapping = True
                        break
                
                if not is_overlapping:
                    merged.append(sound_sub)
        
        # Sort by start time
        return sorted(merged, key=lambda x: x["start"])
    except Exception as e:
        logger.error(f"Error merging subtitles: {e}")
        raise

def format_subtitles(
    subtitles: List[Dict],
    max_chars_per_line: int,
    lines_per_subtitle: int
) -> List[Dict]:
    """Format subtitles according to user preferences"""
    try:
        formatted = []
        
        for sub in subtitles:
            # If it's a non-verbal sound, keep as is
            if sub["type"] == "sound" or sub["text"].startswith("["):
                formatted.append(sub)
                continue
            
            # For speech, format according to preferences
            text = sub["text"]
            max_chars = max_chars_per_line * lines_per_subtitle
            
            # If text is already short enough, keep as is
            if len(text) <= max_chars:
                formatted.append(sub)
                continue
            
            # Otherwise, split into multiple subtitle entries
            words = text.split()
            current_text = ""
            current_chars = 0
            
            for word in words:
                if current_chars + len(word) + 1 <= max_chars:
                    if current_text:
                        current_text += " "
                        current_chars += 1
                    current_text += word
                    current_chars += len(word)
                else:
                    # Create new subtitle entry
                    if current_text:
                        duration = sub["end"] - sub["start"]
                        chars_ratio = len(current_text) / len(text)
                        partial_duration = int(duration * chars_ratio)
                        
                        formatted.append({
                            "start": sub["start"],
                            "end": sub["start"] + partial_duration,
                            "text": current_text,
                            "type": "speech"
                        })
                        
                        sub["start"] += partial_duration
                        current_text = word
                        current_chars = len(word)
            
            # Add last part if any
            if current_text:
                formatted.append({
                    "start": sub["start"],
                    "end": sub["end"],
                    "text": current_text,
                    "type": "speech"
                })
        
        # Sort by start time
        return sorted(formatted, key=lambda x: x["start"])
    except Exception as e:
        logger.error(f"Error formatting subtitles: {e}")
        raise

async def translate_subtitles(
    subtitles: List[Dict],
    source_language: str,
    target_language: str
) -> List[Dict]:
    """
    Translate subtitles to target language using DeepL or OpenAI
    This is a placeholder implementation - in production you would implement the actual API calls
    """
    try:
        # Placeholder implementation
        # In a real implementation, you would:
        # 1. Extract texts to translate
        # 2. Call translation API (DeepL or OpenAI)
        # 3. Replace texts with translations
        
        # For this example, we'll just return the original subtitles
        return subtitles
    except Exception as e:
        logger.error(f"Error translating subtitles: {e}")
        raise

def export_subtitles(
    subtitles: List[Dict],
    output_dir: str,
    filename_base: str,
    output_format: str
) -> str:
    """Export subtitles to the requested format"""
    try:
        output_file = os.path.join(output_dir, f"{filename_base}.{output_format}")
        
        with open(output_file, "w", encoding="utf-8") as f:
            if output_format == OutputFormat.SRT:
                write_srt(f, subtitles)
            elif output_format == OutputFormat.VTT:
                write_vtt(f, subtitles)
            elif output_format == OutputFormat.ASS:
                write_ass(f, subtitles)
            elif output_format == OutputFormat.TXT:
                write_txt(f, subtitles)
        
        return output_file
    except Exception as e:
        logger.error(f"Error exporting subtitles: {e}")
        raise

def write_srt(file, subtitles: List[Dict]):
    """Write subtitles in SRT format"""
    for i, sub in enumerate(subtitles):
        # Convert milliseconds to SRT time format (HH:MM:SS,mmm)
        start_time = format_srt_time(sub["start"])
        end_time = format_srt_time(sub["end"])
        
        file.write(f"{i+1}\n")
        file.write(f"{start_time} --> {end_time}\n")
        file.write(f"{sub['text']}\n\n")

def write_vtt(file, subtitles: List[Dict]):
    """Write subtitles in WebVTT format"""
    file.write("WEBVTT\n\n")
    
    for i, sub in enumerate(subtitles):
        # Convert milliseconds to WebVTT time format (HH:MM:SS.mmm)
        start_time = format_vtt_time(sub["start"])
        end_time = format_vtt_time(sub["end"])
        
        file.write(f"{i+1}\n")
        file.write(f"{start_time} --> {end_time}\n")
        file.write(f"{sub['text']}\n\n")

def write_ass(file, subtitles: List[Dict]):
    """Write subtitles in Advanced SubStation Alpha format"""
    file.write("[Script Info]\n")
    file.write("Title: Generated Subtitles\n")
    file.write("ScriptType: v4.00+\n")
    file.write("PlayResX: 1280\n")
    file.write("PlayResY: 720\n\n")
    
    file.write("[V4+ Styles]\n")
    file.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
    file.write("Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n")
    
    file.write("[Events]\n")
    file.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    
    for sub in subtitles:
        # Convert milliseconds to ASS time format (H:MM:SS.mm)
        start_time = format_ass_time(sub["start"])
        end_time = format_ass_time(sub["end"])
        
        file.write(f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{sub['text']}\n")

def write_txt(file, subtitles: List[Dict]):
    """Write subtitles in plain text format"""
    for sub in subtitles:
        # Convert milliseconds to time format (HH:MM:SS)
        start_time = format_txt_time(sub["start"])
        
        file.write(f"[{start_time}] {sub['text']}\n")

def format_srt_time(ms: int) -> str:
    """Format milliseconds as SRT time (HH:MM:SS,mmm)"""
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def format_vtt_time(ms: int) -> str:
    """Format milliseconds as WebVTT time (HH:MM:SS.mmm)"""
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

def format_ass_time(ms: int) -> str:
    """Format milliseconds as ASS time (H:MM:SS.mm)"""
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    cs = ms // 10  # Centiseconds
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def format_txt_time(ms: int) -> str:
    """Format milliseconds as simple time (HH:MM:SS)"""
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"