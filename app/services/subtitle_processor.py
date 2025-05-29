# app/services/subtitle_processor.py
import asyncio
import asyncpg
import os
import json
import logging
import tempfile
from datetime import datetime
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
import tensorflow as tf
import tensorflow_hub as hub
import numpy as np
import soundfile as sf
import librosa
import assemblyai as aai
from moviepy.video.io.VideoFileClip import VideoFileClip
from app.core.config import settings
from app.core.utils import delete_file, create_output_directory
from app.models.order import OrderStatus, VideoStatus, OutputFormat

logger = logging.getLogger(__name__)

SOUND_LABEL_PATTERNS = {
    "speech": ["speech", "speak", "talk", "voice", "conversation", "dialogue"],
    "music": ["music", "song", "melody", "musical", "instrument", "piano", "guitar", "drum"],
    "laughter": ["laugh", "laughter", "giggle", "chuckle"],
    "applause": ["applause", "clap", "clapping", "ovation"],
    "footsteps": ["footstep", "footsteps", "walk", "step", "running", "jogging"],
    "door": ["door", "slam", "opening", "closing", "knock", "knocking"],
    "car": ["car", "vehicle", "engine", "motor", "automobile", "truck"],
    "thunder": ["thunder", "storm", "lightning", "rumble"],
    "glass": ["glass", "break", "shatter", "crash", "smash"],
    "gunshot": ["gunshot", "gun", "shot", "fire", "shooting", "bullet"],
    "explosion": ["explosion", "blast", "boom", "explode", "detonate"],
    "scream": ["scream", "shout", "yell", "shriek", "cry"],
    "whisper": ["whisper", "murmur", "quiet", "soft"],
    "breathing": ["breath", "breathing", "inhale", "exhale", "gasp"],
    "heartbeat": ["heartbeat", "heart", "pulse", "beat"],
    "cheer": ["cheer", "cheering", "hooray", "celebration"],
    "water": ["water", "splash", "drop", "rain", "pour"],
    "wind": ["wind", "breeze", "gust", "blow"],
    "bell": ["bell", "ring", "chime", "ding"],
    "phone": ["phone", "telephone", "ring", "call"],
    "typing": ["typing", "keyboard", "type", "click"],
    "alarm": ["alarm", "alert", "siren", "warning"],
    "animal": ["dog", "cat", "bird", "bark", "meow", "chirp", "animal"],
    "crowd": ["crowd", "people", "audience", "group"],
    "impact": ["hit", "punch", "slap", "bang", "thud"],
    "mechanical": ["mechanical", "machine", "beep", "buzz", "whir"],
    "fire": ["fire", "crackle", "burn", "flame"],
    "electronic": ["electronic", "digital", "beep", "bleep", "signal"]
}

GENRE_FILTERS = {
    "horror": {
        "priority": ["scream", "thunder", "footsteps", "door", "breathing", "heartbeat", "whisper", "glass", "impact"],
        "allowed": ["scream", "thunder", "footsteps", "door", "breathing", "heartbeat", "whisper", "glass", "impact", "wind", "animal", "mechanical", "fire"],
        "blocked": ["laughter", "applause", "cheer", "music"]
    },
    "comedy": {
        "priority": ["laughter", "applause", "cheer", "impact", "footsteps"],
        "allowed": ["laughter", "applause", "cheer", "music", "footsteps", "door", "impact", "crowd", "animal"],
        "blocked": ["scream", "gunshot", "explosion", "thunder", "breathing", "heartbeat"]
    },
    "romance": {
        "priority": ["music", "whisper", "breathing", "heartbeat", "footsteps"],
        "allowed": ["music", "whisper", "breathing", "heartbeat", "footsteps", "door", "laughter", "water", "wind", "bell"],
        "blocked": ["gunshot", "explosion", "scream", "thunder", "glass", "impact", "alarm"]
    },
    "action": {
        "priority": ["gunshot", "explosion", "car", "footsteps", "door", "glass", "impact"],
        "allowed": ["gunshot", "explosion", "footsteps", "car", "door", "glass", "impact", "thunder", "mechanical", "crowd", "alarm"],
        "blocked": ["whisper", "breathing", "heartbeat", "laughter", "music"]
    },
    "documentary": {
        "priority": ["footsteps", "door", "car", "music", "applause", "crowd"],
        "allowed": ["footsteps", "door", "car", "music", "applause", "water", "wind", "animal", "crowd", "bell", "mechanical"],
        "blocked": ["scream", "gunshot", "explosion", "thunder", "glass"]
    },
    "drama": {
        "priority": ["footsteps", "door", "breathing", "whisper", "music", "car"],
        "allowed": ["footsteps", "door", "breathing", "whisper", "music", "car", "phone", "typing", "crowd", "water", "wind"],
        "blocked": ["explosion", "gunshot", "scream", "thunder"]
    },
    "thriller": {
        "priority": ["footsteps", "door", "breathing", "heartbeat", "phone", "car"],
        "allowed": ["footsteps", "door", "breathing", "heartbeat", "phone", "car", "whisper", "glass", "impact", "mechanical", "alarm"],
        "blocked": ["laughter", "applause", "cheer", "music"]
    },
    "general": {
        "priority": ["footsteps", "door", "laughter", "applause", "music", "car"],
        "allowed": ["footsteps", "door", "laughter", "applause", "music", "car", "phone", "bell", "crowd", "animal", "water", "wind"],
        "blocked": []
    }
}

TRANSLATION_CONFIG = {
    "max_words_per_batch": 500,
    "max_subtitles_per_batch": 50,
    "max_tokens": 2000,
}

async def process_order(order_id: int):
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
        raise

async def generate_speech_subtitles(file_path: str, language: str) -> List[Dict]:
    try:
        if not os.path.exists(file_path) or os.path.getsize(file_path) < 1000:
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
        return []

async def generate_sound_subtitles(file_path: str, genre: str) -> List[Dict]:
    try:
        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        audio_path = temp_file.name
        temp_file.close()
        
        video = VideoFileClip(file_path)
        audio = video.audio
        audio.write_audiofile(audio_path, logger=None)
        audio.close()
        video.close()
        
        yamnet_events = await analyze_with_yamnet(audio_path, genre)
        librosa_events = await analyze_with_librosa(audio_path, genre)
        
        combined_events = combine_sound_events(yamnet_events, librosa_events, genre)
        
        os.unlink(audio_path)
        
        return combined_events
    except Exception as e:
        return []

async def analyze_with_yamnet(audio_path: str, genre: str) -> List[Dict]:
    try:
        yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')
        
        audio_data, sample_rate = sf.read(audio_path)
        
        if len(audio_data.shape) > 1:
            audio_data = np.mean(audio_data, axis=1)
        
        if sample_rate != 16000:
            import scipy.signal
            audio_data = scipy.signal.resample(audio_data, int(len(audio_data) * 16000 / sample_rate))
            sample_rate = 16000
        
        waveform = tf.cast(audio_data, tf.float32)
        
        segment_duration = 2.0
        segment_samples = int(segment_duration * sample_rate)
        
        sound_events = []
        
        class_map_path = yamnet_model.class_map_path().numpy()
        class_names = []
        try:
            with tf.io.gfile.GFile(class_map_path.decode('utf-8')) as csvfile:
                import csv
                reader = csv.DictReader(csvfile)
                for row in reader:
                    class_names.append(row['display_name'])
        except Exception as e:
            return []
        
        for segment_idx, start_sample in enumerate(range(0, len(waveform), segment_samples)):
            end_sample = min(start_sample + segment_samples, len(waveform))
            segment = waveform[start_sample:end_sample]
            
            if len(segment) < segment_samples:
                padding = tf.zeros(segment_samples - len(segment))
                segment = tf.concat([segment, padding], 0)
            
            scores, embeddings, spectrogram = yamnet_model(segment)
            
            for frame_idx in range(scores.shape[0]):
                frame_scores = scores[frame_idx]
                top_indices = tf.nn.top_k(frame_scores, k=5).indices
                
                for class_idx in top_indices:
                    confidence = frame_scores[class_idx].numpy()
                    if confidence > 0.25:
                        class_name = class_names[class_idx]
                        normalized_label = normalize_sound_label(class_name)
                        
                        if normalized_label and should_include_sound(normalized_label, genre):
                            start_time_ms = int((start_sample + frame_idx * 480) / sample_rate * 1000)
                            end_time_ms = start_time_ms + 960
                            
                            sound_events.append({
                                "start": start_time_ms,
                                "end": end_time_ms,
                                "text": normalized_label,
                                "type": "sound",
                                "confidence": float(confidence),
                                "source": "yamnet"
                            })
        
        return sound_events
    except Exception as e:
        return []

async def analyze_with_librosa(audio_path: str, genre: str) -> List[Dict]:
    try:
        y, sr = librosa.load(audio_path, sr=22050)
        
        hop_length = 512
        
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop_length, 
                                                  delta=0.2, units='frames')
        onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop_length)
        
        rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop_length)[0]
        spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr, hop_length=hop_length)[0]
        zcr = librosa.feature.zero_crossing_rate(y, hop_length=hop_length)[0]
        
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop_length)
        beat_times = librosa.frames_to_time(beats, sr=sr, hop_length=hop_length)
        
        sound_events = []
        
        for onset_time in onset_times:
            frame_idx = int(onset_time * sr / hop_length)
            if frame_idx < len(rms):
                energy = rms[frame_idx]
                centroid = spectral_centroids[frame_idx] if frame_idx < len(spectral_centroids) else 0
                rolloff = spectral_rolloff[frame_idx] if frame_idx < len(spectral_rolloff) else 0
                zero_cross = zcr[frame_idx] if frame_idx < len(zcr) else 0
                
                sound_type = classify_onset_type(energy, centroid, rolloff, zero_cross, tempo)
                
                if sound_type and should_include_sound(sound_type, genre):
                    start_time_ms = int(onset_time * 1000)
                    end_time_ms = start_time_ms + 1000
                    
                    sound_events.append({
                        "start": start_time_ms,
                        "end": end_time_ms,
                        "text": sound_type,
                        "type": "sound",
                        "confidence": min(energy * 2, 1.0),
                        "source": "librosa"
                    })
        
        if tempo > 80 and should_include_sound("[Music]", genre):
            for beat_time in beat_times[:10]:
                start_time_ms = int(beat_time * 1000)
                end_time_ms = start_time_ms + 500
                
                sound_events.append({
                    "start": start_time_ms,
                    "end": end_time_ms,
                    "text": "[Music]",
                    "type": "sound",
                    "confidence": 0.8,
                    "source": "librosa"
                })
        
        return sound_events
    except Exception as e:
        return []

def classify_onset_type(energy, centroid, rolloff, zcr, tempo):
    if energy > 0.1:
        if centroid > 3000 and zcr > 0.1:
            return "[Glass breaking]"
        elif centroid > 2500 and energy > 0.3:
            return "[Door slam]"
        elif centroid < 1000 and energy > 0.4:
            return "[Explosion]"
        elif zcr > 0.15:
            return "[Applause]"
        elif centroid > 1500 and energy > 0.2:
            return "[Footsteps]"
        elif centroid < 500 and energy > 0.25:
            return "[Thunder]"
        else:
            return "[Impact sound]"
    return None

def normalize_sound_label(raw_label: str) -> Optional[str]:
    if not raw_label or raw_label.strip() == "":
        return None
    
    raw_lower = raw_label.lower().strip()
    
    for sound_type, patterns in SOUND_LABEL_PATTERNS.items():
        for pattern in patterns:
            if pattern in raw_lower:
                return format_sound_label(sound_type, raw_label)
    
    return format_sound_label("unknown", raw_label)

def format_sound_label(sound_type: str, original_label: str) -> str:
    label_formats = {
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
        "water": "Water sound",
        "wind": "Wind",
        "bell": "Bell ringing",
        "phone": "Phone ringing",
        "typing": "Typing",
        "alarm": "Alarm",
        "animal": "Animal sound",
        "crowd": "Crowd noise",
        "impact": "Impact sound",
        "mechanical": "Mechanical sound",
        "fire": "Fire crackling",
        "electronic": "Electronic sound",
        "unknown": original_label
    }
    
    formatted = label_formats.get(sound_type, original_label)
    return f"[{formatted}]"

def should_include_sound(sound_label: str, genre: str) -> bool:
    if genre not in GENRE_FILTERS:
        return True
    
    sound_key = extract_sound_key(sound_label)
    if not sound_key:
        return False
    
    filters = GENRE_FILTERS[genre]
    
    if sound_key in filters.get("blocked", []):
        return False
    
    allowed_sounds = filters.get("allowed", [])
    if allowed_sounds and sound_key not in allowed_sounds:
        return False
    
    return True

def extract_sound_key(sound_label: str) -> Optional[str]:
    clean_label = sound_label.lower().replace('[', '').replace(']', '').strip()
    
    for sound_type, patterns in SOUND_LABEL_PATTERNS.items():
        for pattern in patterns:
            if pattern in clean_label:
                return sound_type
    
    return None

def get_sound_priority(sound_label: str, genre: str) -> int:
    sound_key = extract_sound_key(sound_label)
    if not sound_key or genre not in GENRE_FILTERS:
        return 0
    
    priority_sounds = GENRE_FILTERS[genre].get("priority", [])
    if sound_key in priority_sounds:
        return priority_sounds.index(sound_key) + 10
    
    return 1

def combine_sound_events(yamnet_events: List[Dict], librosa_events: List[Dict], genre: str) -> List[Dict]:
    all_events = yamnet_events + librosa_events
    
    for event in all_events:
        event["priority"] = get_sound_priority(event["text"], genre)
    
    return deduplicate_sound_events(all_events)

def deduplicate_sound_events(events: List[Dict]) -> List[Dict]:
    if not events:
        return []
    
    events.sort(key=lambda x: (x["start"], -x.get("priority", 0), -x["confidence"]))
    
    deduplicated = []
    for event in events:
        should_add = True
        for existing in deduplicated:
            if (existing["text"] == event["text"] and 
                abs(existing["start"] - event["start"]) < 1500):
                if (event.get("priority", 0) > existing.get("priority", 0) or 
                    (event.get("priority", 0) == existing.get("priority", 0) and 
                     event["confidence"] > existing["confidence"])):
                    deduplicated.remove(existing)
                else:
                    should_add = False
                break
        
        if should_add:
            deduplicated.append(event)
    
    return sorted(deduplicated, key=lambda x: x["start"])

def merge_consecutive_words(word_subtitles: List[Dict], max_duration_ms: int = 3000) -> List[Dict]:
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
        raise

def format_subtitles(
    subtitles: List[Dict],
    max_chars_per_line: int,
    lines_per_subtitle: int
) -> List[Dict]:
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
        raise

async def translate_subtitles(
    subtitles: List[Dict],
    source_language: str,
    target_language: str
) -> List[Dict]:
    try:
        if not settings.OPENAI_API_KEY:
            return subtitles
            
        if source_language == target_language:
            return subtitles
        
        speech_subtitles = [sub for sub in subtitles if sub["type"] == "speech"]
        sound_subtitles = [sub for sub in subtitles if sub["type"] == "sound"]
        
        translated_subtitles = []
        
        if speech_subtitles:
            translated_speech = await translate_texts_in_batches(
                speech_subtitles, source_language, target_language, "speech"
            )
            translated_subtitles.extend(translated_speech)
        
        if sound_subtitles:
            translated_sounds = await translate_texts_in_batches(
                sound_subtitles, source_language, target_language, "sound"
            )
            translated_subtitles.extend(translated_sounds)
        
        translated_subtitles.sort(key=lambda x: x["start"])
        
        return translated_subtitles
    except Exception as e:
        return subtitles

async def translate_texts_in_batches(
    subtitles: List[Dict], 
    source_lang: str, 
    target_lang: str, 
    subtitle_type: str
) -> List[Dict]:
    try:
        if not subtitles:
            return []
        
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        translated_subtitles = []
        
        batches = create_smart_batches(subtitles)
        
        for batch_idx, batch in enumerate(batches):
            try:
                text_items = []
                for i, sub in enumerate(batch):
                    text_items.append(f"{i + 1}. {sub['text']}")
                
                batch_text = "\n".join(text_items)
                
                if subtitle_type == "speech":
                    system_message = (
                        f"Translate the following numbered subtitle texts from {source_lang} to {target_lang}. "
                        f"Maintain the same numbering format and preserve the meaning accurately. "
                        f"Only return the translated texts with their numbers, nothing else."
                    )
                else:
                    system_message = (
                        f"Translate the following numbered sound effect labels from {source_lang} to {target_lang}. "
                        f"Keep the format [sound] with brackets. Maintain the same numbering format. "
                        f"Only return the translated sound labels with their numbers, nothing else."
                    )
                
                response = await make_translation_request(
                    client, system_message, batch_text, batch_idx
                )
                
                if response:
                    batch_translations = parse_translation_response(response, batch)
                    translated_subtitles.extend(batch_translations)
                else:
                    translated_subtitles.extend(batch)
                
                if batch_idx < len(batches) - 1:
                    await asyncio.sleep(0.1)
                    
            except Exception as e:
                translated_subtitles.extend(batch)
        
        return translated_subtitles
        
    except Exception as e:
        return subtitles

def create_smart_batches(subtitles: List[Dict]) -> List[List[Dict]]:
    batches = []
    current_batch = []
    current_word_count = 0
    
    max_words = TRANSLATION_CONFIG["max_words_per_batch"]
    max_subtitles = TRANSLATION_CONFIG["max_subtitles_per_batch"]
    
    for subtitle in subtitles:
        text = subtitle["text"]
        word_count = len(text.split())
        
        if (current_word_count + word_count > max_words or 
            len(current_batch) >= max_subtitles) and current_batch:
            
            batches.append(current_batch)
            current_batch = []
            current_word_count = 0
        
        current_batch.append(subtitle)
        current_word_count += word_count
    
    if current_batch:
        batches.append(current_batch)
    
    return batches

async def make_translation_request(
    client: AsyncOpenAI, 
    system_message: str, 
    batch_text: str, 
    batch_idx: int,
    max_retries: int = 3
) -> str:
    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": batch_text}
                ],
                max_tokens=TRANSLATION_CONFIG["max_tokens"],
                temperature=0.3,
                timeout=30.0
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                return None

def parse_translation_response(translated_text: str, original_batch: List[Dict]) -> List[Dict]:
    try:
        translated_lines = [line.strip() for line in translated_text.split('\n') if line.strip()]
        translated_subtitles = []
        
        for i, original_sub in enumerate(original_batch):
            translated_line = None
            expected_prefix = f"{i + 1}."
            
            for line in translated_lines:
                if line.startswith(expected_prefix):
                    translated_line = line[len(expected_prefix):].strip()
                    break
            
            final_text = translated_line if translated_line else original_sub["text"]
            
            translated_subtitles.append({
                **original_sub,
                "text": final_text
            })
        
        return translated_subtitles
        
    except Exception as e:
        return original_batch

def export_subtitles(
    subtitles: List[Dict],
    output_dir: str,
    filename_base: str,
    output_format: str
) -> str:
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
        raise

def write_srt(file, subtitles: List[Dict]):
    for i, sub in enumerate(subtitles):
        start_time = format_srt_time(sub["start"])
        end_time = format_srt_time(sub["end"])
        
        file.write(f"{i+1}\n")
        file.write(f"{start_time} --> {end_time}\n")
        file.write(f"{sub['text']}\n\n")

def write_vtt(file, subtitles: List[Dict]):
    file.write("WEBVTT\n\n")
    
    for i, sub in enumerate(subtitles):
        start_time = format_vtt_time(sub["start"])
        end_time = format_vtt_time(sub["end"])
        
        file.write(f"{i+1}\n")
        file.write(f"{start_time} --> {end_time}\n")
        file.write(f"{sub['text']}\n\n")

def write_ass(file, subtitles: List[Dict]):
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
    for sub in subtitles:
        start_time = format_txt_time(sub["start"])
        
        file.write(f"[{start_time}] {sub['text']}\n")

def format_srt_time(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def format_vtt_time(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

def format_ass_time(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    cs = ms // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def format_txt_time(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"