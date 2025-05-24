import asyncio
import asyncpg
import os
import logging
import shutil
from typing import Optional, Dict, Any
from datetime import datetime
import assemblyai as aai
from app.core.config import settings
from app.core.database import get_db_connection
from app.models.order import Genre, OrderStatus, OutputFormat, PaymentStatus
# from app.models.subtitle import Genre, OutputFormat

logger = logging.getLogger(__name__)

aai.settings.api_key = settings.ASSEMBLYAI_API_KEY

async def process_order(order_id: int):
    """Process an order by generating subtitles for all videos"""
    conn = None
    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
        
        order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            logger.error(f"Order {order_id} not found")
            return
        
        if order["payment_status"] != PaymentStatus.PAID:
            logger.error(f"Order {order_id} payment not confirmed")
            return
        
        await conn.execute("""
            UPDATE orders 
            SET status = $1, updated_at = CURRENT_TIMESTAMP 
            WHERE id = $2
        """, OrderStatus.PROCESSING, order_id)
        
        videos = await conn.fetch("SELECT * FROM videos WHERE order_id = $1", order_id)
        config = await conn.fetchrow("SELECT * FROM subtitle_configs WHERE order_id = $1", order_id)
        
        if not videos or not config:
            logger.error(f"No videos or config found for order {order_id}")
            await mark_order_failed(conn, order_id, "Missing videos or configuration")
            return
        
        success_count = 0
        for video in videos:
            try:
                await process_video_subtitles(conn, video, config, order)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to process video {video['id']}: {e}")
                await conn.execute("""
                    UPDATE videos 
                    SET status = $1, updated_at = CURRENT_TIMESTAMP, error_message = $2
                    WHERE id = $3
                """, OrderStatus.FAILED, str(e), video['id'])
        
        if success_count == len(videos):
            await conn.execute("""
                UPDATE orders 
                SET status = $1, updated_at = CURRENT_TIMESTAMP 
                WHERE id = $2
            """, OrderStatus.COMPLETED, order_id)
            logger.info(f"Order {order_id} completed successfully")
        elif success_count > 0:
            await conn.execute("""
                UPDATE orders 
                SET status = $1, updated_at = CURRENT_TIMESTAMP 
                WHERE id = $2
            """, OrderStatus.PARTIALLY_COMPLETED, order_id)
            logger.warning(f"Order {order_id} partially completed ({success_count}/{len(videos)})")
        else:
            await mark_order_failed(conn, order_id, "All videos failed to process")
    
    except Exception as e:
        logger.error(f"Error processing order {order_id}: {e}")
        if conn:
            await mark_order_failed(conn, order_id, f"Processing error: {str(e)}")
    finally:
        if conn:
            await conn.close()

async def process_video_subtitles(conn: asyncpg.Connection, video: dict, config: dict, order: dict):
    """Generate subtitles for a single video using AssemblyAI"""
    try:
        video_path = video['file_path']
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        await conn.execute("""
            UPDATE videos 
            SET status = $1, updated_at = CURRENT_TIMESTAMP 
            WHERE id = $2
        """, OrderStatus.PROCESSING, video['id'])
        
        transcription_config = create_transcription_config(config)
        transcriber = aai.Transcriber()
        
        logger.info(f"Starting transcription for video {video['id']} using AssemblyAI")
        transcript = transcriber.transcribe(video_path, config=transcription_config)
        
        if transcript.status == aai.TranscriptStatus.error:
            raise Exception(f"AssemblyAI transcription failed: {transcript.error}")
        
        output_dir = os.path.join(settings.OUTPUT_DIR, str(order['user_id']), str(order['id']))
        os.makedirs(output_dir, exist_ok=True)
        
        base_filename = os.path.splitext(video['original_filename'])[0]
        
        subtitle_content = generate_subtitle_content(transcript, config)
        subtitle_filename = f"{base_filename}.{config['output_format']}"
        subtitle_path = os.path.join(output_dir, subtitle_filename)
        
        with open(subtitle_path, 'w', encoding='utf-8') as f:
            f.write(subtitle_content)
        
        await conn.fetchval("""
            INSERT INTO subtitle_files (video_id, config_id, file_path, file_format, qa_status, transcript_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, video['id'], config['id'], subtitle_path, config['output_format'], 'pending', transcript.id)
        
        await conn.execute("""
            UPDATE videos 
            SET status = $1, updated_at = CURRENT_TIMESTAMP 
            WHERE id = $2
        """, OrderStatus.COMPLETED, video['id'])
        
        logger.info(f"Subtitles generated successfully for video {video['id']}")
        
    except Exception as e:
        logger.error(f"Error processing video {video['id']}: {e}")
        raise

def create_transcription_config(config: dict) -> aai.TranscriptionConfig:
    """Create AssemblyAI transcription configuration based on subtitle config"""
    transcription_config = aai.TranscriptionConfig()
    
    if config.get('source_language'):
        language_map = {
            'en': aai.LanguageCode.en_us,
            'es': aai.LanguageCode.es,
            'fr': aai.LanguageCode.fr,
            'de': aai.LanguageCode.de,
            'it': aai.LanguageCode.it,
            'pt': aai.LanguageCode.pt,
            'ru': aai.LanguageCode.ru,
            'ja': aai.LanguageCode.ja,
            'ko': aai.LanguageCode.ko,
            'zh': aai.LanguageCode.zh,
            'hi': aai.LanguageCode.hi,
        }
        source_lang = config['source_language']
        if source_lang in language_map:
            transcription_config.language_code = language_map[source_lang]
        else:
            logger.warning(f"Unsupported language '{source_lang}', defaulting to English")
            transcription_config.language_code = aai.LanguageCode.en_us
    
    transcription_config.punctuate = True
    transcription_config.format_text = True
    
    if config.get('accessibility_mode'):
        transcription_config.speaker_labels = True
        transcription_config.filter_profanity = True
    
    genre = config.get('genre', Genre.GENERAL)
    if genre == Genre.MEETING:
        transcription_config.speaker_labels = True
        transcription_config.auto_chapters = True
    elif genre == Genre.PODCAST:
        transcription_config.speaker_labels = True
        transcription_config.auto_highlights = True
    elif genre == Genre.LECTURE:
        transcription_config.auto_chapters = True
    elif genre == Genre.INTERVIEW:
        transcription_config.speaker_labels = True
    
    if config.get('non_verbal_only_mode'):
        transcription_config.filter_profanity = True
        transcription_config.redact_pii = True
    
    return transcription_config

def generate_subtitle_content(transcript: aai.Transcript, config: dict) -> str:
    """Generate subtitle content in the requested format"""
    output_format = config.get('output_format', OutputFormat.SRT)
    target_language = config.get('target_language')
    
    if target_language and target_language != config.get('source_language'):
        subtitle_content = translate_subtitles(transcript, target_language, output_format, config)
    else:
        if output_format == OutputFormat.SRT:
            subtitle_content = transcript.export_subtitles_srt()
        elif output_format == OutputFormat.VTT:
            subtitle_content = transcript.export_subtitles_vtt()
        elif output_format == OutputFormat.ASS:
            subtitle_content = convert_to_ass_format(transcript, config)
        else:
            subtitle_content = transcript.export_subtitles_srt()
    
    return apply_subtitle_formatting(subtitle_content, config)

def translate_subtitles(transcript: aai.Transcript, target_language: str, output_format: str, config: dict) -> str:
    """Translate subtitles to target language using LeMUR"""
    try:
        language_names = {
            'es': 'Spanish',
            'fr': 'French',
            'de': 'German',
            'it': 'Italian',
            'pt': 'Portuguese',
            'ru': 'Russian',
            'ja': 'Japanese',
            'ko': 'Korean',
            'zh': 'Chinese',
            'hi': 'Hindi',
            'ar': 'Arabic'
        }
        
        target_language_name = language_names.get(target_language, target_language)
        
        prompt = f"""
        Translate the following transcript to {target_language_name}. 
        Maintain the timing structure and format it as {output_format.upper()} subtitles.
        Keep the same sentence breaks and timing as much as possible.
        Make sure translations are natural and appropriate for subtitles.
        
        Original transcript:
        {transcript.text}
        """
        
        result = transcript.lemur.task(prompt)
        return result.response
        
    except Exception as e:
        logger.warning(f"Translation failed, using original: {e}")
        if output_format == OutputFormat.SRT:
            return transcript.export_subtitles_srt()
        elif output_format == OutputFormat.VTT:
            return transcript.export_subtitles_vtt()
        else:
            return transcript.export_subtitles_srt()

def convert_to_ass_format(transcript: aai.Transcript, config: dict) -> str:
    """Convert transcript to Advanced SubStation Alpha (.ass) format"""
    ass_header = """[Script Info]
Title: Generated Subtitles
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,16,&H00FFFFFF,&H000000FF,&H00000000,&H00808080,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    srt_content = transcript.export_subtitles_srt()
    ass_events = ""
    
    import re
    srt_pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\d+\n|\Z)'
    matches = re.findall(srt_pattern, srt_content, re.DOTALL)
    
    for match in matches:
        start_time = match[1].replace(',', '.')
        end_time = match[2].replace(',', '.')
        text = match[3].replace('\n', '\\N')
        
        start_ass = convert_srt_time_to_ass(start_time)
        end_ass = convert_srt_time_to_ass(end_time)
        
        ass_events += f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}\n"
    
    return ass_header + ass_events

def convert_srt_time_to_ass(srt_time: str) -> str:
    """Convert SRT time format to ASS time format"""
    time_part, ms_part = srt_time.split('.')
    return f"{time_part}.{ms_part[:2]}"

def apply_subtitle_formatting(content: str, config: dict) -> str:
    """Apply formatting rules based on configuration"""
    max_chars = config.get('max_chars_per_line', 42)
    max_lines = config.get('lines_per_subtitle', 2)
    
    lines = content.split('\n')
    formatted_lines = []
    
    for line in lines:
        if '-->' in line or line.isdigit() or not line.strip():
            formatted_lines.append(line)
        else:
            formatted_line = format_subtitle_line(line, max_chars, max_lines)
            formatted_lines.append(formatted_line)
    
    return '\n'.join(formatted_lines)

def format_subtitle_line(text: str, max_chars: int, max_lines: int) -> str:
    """Format a subtitle line to respect character and line limits"""
    if len(text) <= max_chars:
        return text
    
    words = text.split()
    lines = []
    current_line = ""
    
    for word in words:
        test_line = f"{current_line} {word}".strip()
        if len(test_line) <= max_chars:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
                current_line = word
            else:
                lines.append(word[:max_chars])
                current_line = word[max_chars:] if len(word) > max_chars else ""
            
            if len(lines) >= max_lines:
                break
    
    if current_line and len(lines) < max_lines:
        lines.append(current_line)
    
    return '\n'.join(lines)

async def mark_order_failed(conn: asyncpg.Connection, order_id: int, error_message: str):
    """Mark an order as failed with error message"""
    try:
        await conn.execute("""
            UPDATE orders 
            SET status = $1, updated_at = CURRENT_TIMESTAMP, error_message = $2
            WHERE id = $3
        """, OrderStatus.FAILED, error_message, order_id)
    except Exception:
        # Fallback if error_message column doesn't exist
        await conn.execute("""
            UPDATE orders 
            SET status = $1, updated_at = CURRENT_TIMESTAMP 
            WHERE id = $2
        """, OrderStatus.FAILED, order_id)
    
    logger.error(f"Order {order_id} marked as failed: {error_message}")

async def reprocess_order(order_id: int, notes: Optional[str] = None):
    """Reprocess a failed or completed order"""
    conn = None
    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
        
        order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found")
        
        if order["payment_status"] != PaymentStatus.PAID:
            raise ValueError("Order must be paid to reprocess")
        
        await conn.execute("""
            UPDATE orders 
            SET status = $1, updated_at = CURRENT_TIMESTAMP, admin_notes = $2
            WHERE id = $3
        """, OrderStatus.PROCESSING, notes, order_id)
        
        subtitle_files = await conn.fetch("""
            SELECT sf.* FROM subtitle_files sf
            JOIN videos v ON sf.video_id = v.id
            WHERE v.order_id = $1
        """, order_id)
        
        for subtitle_file in subtitle_files:
            if os.path.exists(subtitle_file['file_path']):
                os.remove(subtitle_file['file_path'])
        
        await conn.execute("""
            DELETE FROM subtitle_files 
            WHERE video_id IN (SELECT id FROM videos WHERE order_id = $1)
        """, order_id)
        
        await process_order(order_id)
        
    except Exception as e:
        logger.error(f"Error reprocessing order {order_id}: {e}")
        if conn:
            await mark_order_failed(conn, order_id, f"Reprocessing error: {str(e)}")
        raise
    finally:
        if conn:
            await conn.close()

def get_video_duration(file_path: str) -> float:
    """Get video duration using ffprobe"""
    try:
        import subprocess
        result = subprocess.run([
            'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', file_path
        ], capture_output=True, text=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"Could not get video duration: {e}")
        return 0.0

async def update_subtitle_qa_status(conn: asyncpg.Connection, subtitle_id: int, qa_status: str, qa_notes: Optional[str] = None):
    """Update QA status for a subtitle file"""
    await conn.execute("""
        UPDATE subtitle_files 
        SET qa_status = $1, qa_notes = $2, updated_at = CURRENT_TIMESTAMP
        WHERE id = $3
    """, qa_status, qa_notes, subtitle_id)
    
    if qa_status == 'rejected':
        subtitle_file = await conn.fetchrow("SELECT * FROM subtitle_files WHERE id = $1", subtitle_id)
        if subtitle_file:
            video = await conn.fetchrow("SELECT * FROM videos WHERE id = $1", subtitle_file['video_id'])
            if video:
                await conn.execute("""
                    UPDATE orders 
                    SET status = $1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = $2
                """, OrderStatus.REQUIRES_REVIEW, video['order_id'])