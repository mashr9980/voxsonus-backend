# app/services/subtitle_processor.py
import asyncio
import asyncpg
import os
import json
import logging
import requests
import time
import tempfile
from datetime import datetime
from typing import List, Dict, Any
from openai import AsyncOpenAI
import tensorflow as tf
import tensorflow_hub as hub
import numpy as np
import soundfile as sf
import assemblyai as aai
from moviepy.video.io.VideoFileClip import VideoFileClip
from app.core.config import settings
from app.core.utils import delete_file, create_output_directory
from app.models.order import OrderStatus, VideoStatus, OutputFormat

logger = logging.getLogger(__name__)

# Genre-based sound filtering rules
GENRE_SOUND_FILTERS = {
    "horror": {
        "allowed": ["scream", "thunder", "door_slam", "footsteps", "whisper", "breathing", "heartbeat", "glass_break", "gunshot", "explosion"],
        "blocked": ["laughter", "applause", "music", "cheer"]
    },
    "comedy": {
        "allowed": ["laughter", "applause", "cheer", "music", "footsteps", "door_slam"],
        "blocked": ["scream", "gunshot", "explosion", "thunder"]
    },
    "romance": {
        "allowed": ["music", "whisper", "breathing", "footsteps", "door_slam", "laughter"],
        "blocked": ["gunshot", "explosion", "scream", "thunder"]
    },
    "action": {
        "allowed": ["gunshot", "explosion", "footsteps", "car_engine", "door_slam", "glass_break", "thunder"],
        "blocked": ["whisper", "breathing", "laughter"]
    },
    "documentary": {
        "allowed": ["footsteps", "door_slam", "car_engine", "music", "applause"],
        "blocked": ["scream", "gunshot", "explosion"]
    },
    "general": {
        "allowed": ["footsteps", "door_slam", "laughter", "applause", "music", "car_engine"],
        "blocked": []
    }
}

# YAMNet label to normalized format mapping
YAMNET_LABEL_MAPPING = {
    "Speech": "Speech",
    "Music": "Music",
    "Laughter": "Laughter",
    "Applause": "Applause",
    "Footsteps": "Footsteps",
    "Door": "Door slam",
    "Car": "Car engine",
    "Thunder": "Thunder",
    "Glass": "Glass breaking",
    "Gunshot": "Gunshot",
    "Explosion": "Explosion", 
    "Scream": "Scream",
    "Whisper": "Whisper",
    "Breathing": "Breathing",
    "Heartbeat": "Heartbeat",
    "Cheer": "Cheering"
}

async def process_order(order_id: int):
    """Process a paid order by generating subtitles for all videos"""
    conn = None
    try:
        conn = await asyncpg.connect(settings.DATABASE_URL)
        
        await conn.execute(
            "UPDATE orders SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            OrderStatus.PROCESSING, order_id
        )
        
        order = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        user_id = order["user_id"]
        
        subtitle_config = await conn.fetchrow(
            "SELECT * FROM subtitle_configs WHERE order_id = $1", order_id
        )
        
        videos = await conn.fetch(
            "SELECT * FROM videos WHERE order_id = $1", order_id
        )
        
        output_dir = create_output_directory(user_id, order_id)
        
        for video in videos:
            try:
                await conn.execute(
                    "UPDATE videos SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                    VideoStatus.PROCESSING, video["id"]
                )
                
                subtitle_files = await generate_subtitles(
                    video, subtitle_config, output_dir, conn
                )
                
                await conn.execute(
                    "UPDATE videos SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                    VideoStatus.COMPLETED, video["id"]
                )
                
                await delete_file(video["file_path"])
            except Exception as e:
                logger.error(f"Error processing video {video['id']} for order {order_id}: {e}")
                
                await conn.execute(
                    "UPDATE videos SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                    VideoStatus.FAILED, video["id"]
                )
        
        all_videos_processed = True
        videos_status = await conn.fetch(
            "SELECT status FROM videos WHERE order_id = $1", order_id
        )
        
        for video_status in videos_status:
            if video_status["status"] not in [VideoStatus.COMPLETED, VideoStatus.FAILED]:
                all_videos_processed = False
                break
        
        final_status = OrderStatus.COMPLETED if all_videos_processed else OrderStatus.FAILED
        await conn.execute(
            "UPDATE orders SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            final_status, order_id
        )
    except Exception as e:
        logger.error(f"Error processing order {order_id}: {e}")
        
        if conn:
            await conn.execute(
                "UPDATE orders SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
                OrderStatus.FAILED, order_id
            )
    finally:
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
        speech_subtitles = await generate_speech_subtitles(
            video["file_path"], config["source_language"]
        )
        
        sound_subtitles = await generate_sound_subtitles(
            video["file_path"], config["genre"]
        )
        
        merged_subtitles = merge_subtitles(
            speech_subtitles, 
            sound_subtitles, 
            config["accessibility_mode"],
            config["non_verbal_only_mode"]
        )
        
        formatted_subtitles = format_subtitles(
            merged_subtitles,
            config["max_chars_per_line"],
            config["lines_per_subtitle"]
        )
        
        if config["target_language"] and config["target_language"] != config["source_language"]:
            translated_subtitles = await translate_subtitles(
                formatted_subtitles,
                config["source_language"],
                config["target_language"]
            )
        else:
            translated_subtitles = formatted_subtitles
        
        subtitle_files = []
        output_format = config["output_format"]
        
        filename_base = f"{os.path.splitext(video['original_filename'])[0]}"
        
        output_file = export_subtitles(
            translated_subtitles,
            output_dir,
            filename_base,
            output_format
        )
        subtitle_files.append(output_file)
        
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
    """Generate speech subtitles using AssemblyAI API"""
    try:
        if not os.path.exists(file_path) or os.path.getsize(file_path) < 1000:
            logger.error(f"Video file not found or too small: {file_path}")
            return []
        
        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        audio_path = temp_file.name
        temp_file.close()
        
        video = VideoFileClip(file_path)
        audio = video.audio
        audio.write_audiofile(audio_path, logger=None)
        audio.close()
        video.close()
        
        aai.settings.api_key = settings.ASSEMBLY_AI_API_KEY
        transcriber = aai.Transcriber()
        config = aai.TranscriptionConfig(
            speech_model=aai.SpeechModel.slam_1,
            language_code=language if language != 'auto' else None,
            punctuate=True,
            format_text=True
        )
        
        transcript = transcriber.transcribe(audio_path, config)
        
        os.unlink(audio_path)
        
        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"AssemblyAI transcription failed: {transcript.error}")
            return []
        
        subtitles = []
        if transcript.words:
            for word_info in transcript.words:
                subtitles.append({
                    "start": word_info.start,
                    "end": word_info.end,
                    "text": word_info.text,
                    "type": "speech"
                })
        
        return merge_consecutive_words(subtitles) if subtitles else []
    except Exception as e:
        logger.error(f"Error generating speech subtitles: {e}")
        return []

async def generate_sound_subtitles(file_path: str, genre: str) -> List[Dict]:
    """Generate non-verbal sound subtitles using YAMNet"""
    try:
        logger.info(f"Starting sound detection for genre: {genre}")
        
        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        audio_path = temp_file.name
        temp_file.close()
        
        video = VideoFileClip(file_path)
        audio = video.audio
        audio.write_audiofile(audio_path, logger=None)
        audio.close()
        video.close()
        
        logger.info("Loading YAMNet model...")
        yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')
        
        audio_data, sample_rate = sf.read(audio_path)
        logger.info(f"Audio loaded: {len(audio_data)} samples at {sample_rate}Hz")
        
        if len(audio_data.shape) > 1:
            audio_data = np.mean(audio_data, axis=1)
        
        if sample_rate != 16000:
            import scipy.signal
            audio_data = scipy.signal.resample(audio_data, int(len(audio_data) * 16000 / sample_rate))
            sample_rate = 16000
            logger.info("Audio resampled to 16kHz")
        
        waveform = tf.cast(audio_data, tf.float32)
        
        segment_duration = 5.0
        segment_samples = int(segment_duration * sample_rate)
        
        sound_events = []
        total_segments = len(waveform) // segment_samples + 1
        logger.info(f"Processing {total_segments} audio segments...")
        
        for segment_idx, start_sample in enumerate(range(0, len(waveform), segment_samples)):
            end_sample = min(start_sample + segment_samples, len(waveform))
            segment = waveform[start_sample:end_sample]
            
            if len(segment) < segment_samples:
                padding = tf.zeros(segment_samples - len(segment))
                segment = tf.concat([segment, padding], 0)
            
            scores, embeddings, spectrogram = yamnet_model(segment)
            
            class_names_path = yamnet_model.class_map_path().numpy().decode('utf-8')
            with open(class_names_path, 'r') as f:
                class_labels = [line.strip() for line in f.readlines()]
            
            top_class_indices = tf.argmax(scores, axis=1)
            
            for i, class_idx in enumerate(top_class_indices):
                confidence = scores[i][class_idx].numpy()
                if confidence > 0.2:  # Lower threshold to detect more sounds
                    class_name = class_labels[class_idx]
                    logger.info(f"Raw detection: {class_name} (confidence: {confidence:.2f})")
                    normalized_label = normalize_sound_label(class_name)
                    
                    if normalized_label:
                        logger.info(f"Normalized: {class_name} -> {normalized_label}")
                        
                        if should_include_sound(normalized_label, genre):
                            start_time_ms = int((start_sample + i * 960) / sample_rate * 1000)
                            end_time_ms = start_time_ms + 960
                            
                            sound_events.append({
                                "start": start_time_ms,
                                "end": end_time_ms,
                                "text": normalized_label,
                                "type": "sound",
                                "confidence": float(confidence)
                            })
                            logger.info(f"Added sound event: {normalized_label} at {start_time_ms}ms")
                        else:
                            logger.info(f"Sound {normalized_label} filtered out by genre {genre}")
                    else:
                        logger.info(f"No normalized label found for: {class_name}")
        
        os.unlink(audio_path)
        
        logger.info(f"Sound detection completed. Found {len(sound_events)} events before deduplication.")
        deduplicated_events = deduplicate_sound_events(sound_events)
        logger.info(f"After deduplication: {len(deduplicated_events)} sound events")
        
        return deduplicated_events
    except Exception as e:
        logger.error(f"Error generating sound subtitles: {e}")
        return []



def normalize_sound_label(yamnet_label: str) -> str:
    """Normalize YAMNet labels to FCC-compliant format"""
    yamnet_label_lower = yamnet_label.lower()
    
    # More comprehensive mapping
    label_mappings = {
        "speech": "Speech",
        "music": "Music", 
        "laughter": "Laughter",
        "applause": "Applause",
        "footsteps": "Footsteps",
        "door": "Door slam",
        "car": "Car engine",
        "thunder": "Thunder",
        "glass": "Glass breaking",
        "gunshot": "Gunshot",
        "explosion": "Explosion",
        "scream": "Scream",
        "whisper": "Whisper",
        "breathing": "Breathing",
        "heartbeat": "Heartbeat",
        "cheer": "Cheering",
        "knock": "Knocking",
        "bell": "Bell ringing",
        "phone": "Phone ringing",
        "water": "Water sound",
        "wind": "Wind",
        "rain": "Rain",
        "engine": "Engine",
        "typing": "Typing",
        "click": "Clicking",
        "beep": "Beep",
        "alarm": "Alarm",
        "siren": "Siren"
    }
    
    for key, normalized in label_mappings.items():
        if key in yamnet_label_lower:
            return f"[{normalized}]"
    
    # If no match found, return original with brackets for debugging
    return f"[{yamnet_label}]"

def should_include_sound(sound_label: str, genre: str) -> bool:
    """Filter sounds based on genre preferences"""
    if genre not in GENRE_SOUND_FILTERS:
        logger.info(f"Genre {genre} not in filters, including all sounds")
        return True
    
    filters = GENRE_SOUND_FILTERS[genre]
    sound_key = sound_label.lower().replace('[', '').replace(']', '').replace(' ', '_')
    
    logger.info(f"Checking sound '{sound_key}' against genre '{genre}' filters")
    
    if sound_key in filters["blocked"]:
        logger.info(f"Sound '{sound_key}' is blocked for genre '{genre}'")
        return False
    
    if filters["allowed"] and sound_key not in filters["allowed"]:
        logger.info(f"Sound '{sound_key}' not in allowed list for genre '{genre}'")
        return False
    
    logger.info(f"Sound '{sound_key}' is allowed for genre '{genre}'")
    return True

def deduplicate_sound_events(events: List[Dict]) -> List[Dict]:
    """Remove duplicate and overlapping sound events"""
    if not events:
        return []
    
    events.sort(key=lambda x: (x["start"], -x["confidence"]))
    
    deduplicated = []
    for event in events:
        should_add = True
        for existing in deduplicated:
            if (existing["text"] == event["text"] and 
                existing["start"] <= event["start"] <= existing["end"]):
                should_add = False
                break
        
        if should_add:
            deduplicated.append(event)
    
    return deduplicated

def merge_consecutive_words(word_subtitles: List[Dict], max_duration_ms: int = 3000) -> List[Dict]:
    """Merge consecutive words into phrases for better readability"""
    if not word_subtitles:
        return []
    
    merged = []
    current_phrase = {
        "start": word_subtitles[0]["start"],
        "end": word_subtitles[0]["end"],
        "text": word_subtitles[0]["text"],
        "type": "speech"
    }
    
    for i in range(1, len(word_subtitles)):
        word = word_subtitles[i]
        
        if (word["start"] - current_phrase["end"] < 500 and 
            word["end"] - current_phrase["start"] < max_duration_ms):
            current_phrase["text"] += " " + word["text"]
            current_phrase["end"] = word["end"]
        else:
            merged.append(current_phrase)
            current_phrase = {
                "start": word["start"],
                "end": word["end"],
                "text": word["text"],
                "type": "speech"
            }
    
    merged.append(current_phrase)
    return merged

def merge_subtitles(
    speech_subtitles: List[Dict],
    sound_subtitles: List[Dict],
    accessibility_mode: bool,
    non_verbal_only_mode: bool
) -> List[Dict]:
    """Merge speech and sound subtitles according to user preferences"""
    try:
        merged = []
        
        if non_verbal_only_mode:
            return sorted(sound_subtitles, key=lambda x: x["start"])
        
        merged = speech_subtitles.copy()
        
        for sound_sub in sound_subtitles:
            if accessibility_mode:
                merged.append(sound_sub)
            else:
                is_overlapping = False
                for speech_sub in speech_subtitles:
                    if (sound_sub["start"] <= speech_sub["end"] and 
                        sound_sub["end"] >= speech_sub["start"]):
                        is_overlapping = True
                        break
                
                if not is_overlapping:
                    merged.append(sound_sub)
        
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
            if sub["type"] == "sound":
                formatted.append(sub)
                continue
            
            text = sub["text"]
            max_chars = max_chars_per_line * lines_per_subtitle
            
            if len(text) <= max_chars:
                formatted.append(sub)
                continue
            
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
            
            if current_text:
                formatted.append({
                    "start": sub["start"],
                    "end": sub["end"],
                    "text": current_text,
                    "type": "speech"
                })
        
        return sorted(formatted, key=lambda x: x["start"])
    except Exception as e:
        logger.error(f"Error formatting subtitles: {e}")
        raise

async def translate_subtitles(
    subtitles: List[Dict],
    source_language: str,
    target_language: str
) -> List[Dict]:
    """Translate subtitles to target language using OpenAI GPT-4o in batches"""
    try:
        if not settings.OPENAI_API_KEY:
            logger.warning("No OpenAI API key available, skipping translation")
            return subtitles
            
        if source_language == target_language:
            logger.info("Source and target languages are the same, skipping translation")
            return subtitles
        
        # Separate speech and sound subtitles
        speech_subtitles = [sub for sub in subtitles if sub["type"] == "speech"]
        sound_subtitles = [sub for sub in subtitles if sub["type"] == "sound"]
        
        translated_subtitles = []
        
        # Batch translate speech subtitles
        if speech_subtitles:
            logger.info(f"Translating {len(speech_subtitles)} speech subtitles in batch")
            translated_speech = await batch_translate_speech(speech_subtitles, source_language, target_language)
            translated_subtitles.extend(translated_speech)
        
        # Batch translate sound subtitles
        if sound_subtitles:
            logger.info(f"Translating {len(sound_subtitles)} sound subtitles in batch")
            translated_sounds = await batch_translate_sounds(sound_subtitles, target_language)
            translated_subtitles.extend(translated_sounds)
        
        # Sort by start time
        translated_subtitles.sort(key=lambda x: x["start"])
        
        return translated_subtitles
    except Exception as e:
        logger.error(f"Error translating subtitles: {e}")
        return subtitles

async def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using OpenAI"""
    try:
        if settings.OPENAI_API_KEY:
            return await translate_with_openai(text, source_lang, target_lang)
        else:
            return text
    except Exception as e:
        logger.error(f"Error translating text: {e}")
        return text

async def batch_translate_speech(speech_subtitles: List[Dict], source_lang: str, target_lang: str) -> List[Dict]:
    """Batch translate speech subtitles using GPT-4o"""
    try:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        # Create numbered text list for batch translation
        text_list = []
        for i, sub in enumerate(speech_subtitles):
            text_list.append(f"{i+1}. {sub['text']}")
        
        batch_text = "\n".join(text_list)
        
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": f"Translate the following numbered subtitle texts from {source_lang} to {target_lang}. Maintain the same numbering format. Only return the translated texts with their numbers."
                },
                {"role": "user", "content": batch_text}
            ],
            max_tokens=2000,
            temperature=0.3
        )
        
        translated_text = response.choices[0].message.content.strip()
        
        # Parse the translated response back into individual subtitles
        translated_lines = translated_text.split('\n')
        translated_subtitles = []
        
        for i, sub in enumerate(speech_subtitles):
            # Find the corresponding translated line
            translated_line = None
            for line in translated_lines:
                if line.strip().startswith(f"{i+1}."):
                    translated_line = line.strip()[len(f"{i+1}."):].strip()
                    break
            
            # If translation found, use it; otherwise keep original
            translated_text = translated_line if translated_line else sub["text"]
            
            translated_subtitles.append({
                **sub,
                "text": translated_text
            })
        
        return translated_subtitles
    except Exception as e:
        logger.error(f"Error in batch speech translation: {e}")
        return speech_subtitles

async def batch_translate_sounds(sound_subtitles: List[Dict], target_lang: str) -> List[Dict]:
    """Batch translate sound subtitles using GPT-4o"""
    try:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        # Create numbered sound list for batch translation
        sound_list = []
        for i, sub in enumerate(sound_subtitles):
            sound_list.append(f"{i+1}. {sub['text']}")
        
        batch_text = "\n".join(sound_list)
        
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": f"Translate the following numbered sound effect labels to {target_lang}. Keep the format [sound] with brackets. Maintain the same numbering format. Only return the translated sound labels with their numbers."
                },
                {"role": "user", "content": batch_text}
            ],
            max_tokens=1000,
            temperature=0.3
        )
        
        translated_text = response.choices[0].message.content.strip()
        
        # Parse the translated response back into individual subtitles
        translated_lines = translated_text.split('\n')
        translated_subtitles = []
        
        for i, sub in enumerate(sound_subtitles):
            # Find the corresponding translated line
            translated_line = None
            for line in translated_lines:
                if line.strip().startswith(f"{i+1}."):
                    translated_line = line.strip()[len(f"{i+1}."):].strip()
                    break
            
            # If translation found, use it; otherwise keep original
            translated_text = translated_line if translated_line else sub["text"]
            
            translated_subtitles.append({
                **sub,
                "text": translated_text
            })
        
        return translated_subtitles
    except Exception as e:
        logger.error(f"Error in batch sound translation: {e}")
        return sound_subtitles

async def translate_with_openai(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using OpenAI API"""
    try:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": f"Translate the following text from {source_lang} to {target_lang}. Only return the translation."
                },
                {"role": "user", "content": text}
            ],
            max_tokens=400,
            temperature=0.3
        )
        
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error with OpenAI translation: {e}")
        return text

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
        start_time = format_srt_time(sub["start"])
        end_time = format_srt_time(sub["end"])
        
        file.write(f"{i+1}\n")
        file.write(f"{start_time} --> {end_time}\n")
        file.write(f"{sub['text']}\n\n")

def write_vtt(file, subtitles: List[Dict]):
    """Write subtitles in WebVTT format"""
    file.write("WEBVTT\n\n")
    
    for i, sub in enumerate(subtitles):
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
        start_time = format_ass_time(sub["start"])
        end_time = format_ass_time(sub["end"])
        
        file.write(f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{sub['text']}\n")

def write_txt(file, subtitles: List[Dict]):
    """Write subtitles in plain text format"""
    for sub in subtitles:
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
    cs = ms // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def format_txt_time(ms: int) -> str:
    """Format milliseconds as simple time (HH:MM:SS)"""
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"