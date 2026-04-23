use anyhow::{Context, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Sample;
use rubato::{Resampler, SincFixedIn, SincInterpolationType, SincInterpolationParameters, WindowFunction};
use serde::Serialize;
use tokio::sync::mpsc;
use tracing::{info, error};

use std::path::PathBuf;

#[derive(Serialize)]
struct TranscriptionEvent {
    text: String,
}

/// Try to locate libonnxruntime.so for the `ort` crate's `load-dynamic` feature.
fn find_ort_dylib() -> Option<PathBuf> {
    // 1. Try asking Python where onnxruntime is installed
    if let Ok(output) = std::process::Command::new("python3")
        .args(["-c", "import onnxruntime; import os; print(os.path.join(os.path.dirname(onnxruntime.__file__), 'capi'))"])
        .output()
    {
        if output.status.success() {
            let capi_dir = String::from_utf8_lossy(&output.stdout).trim().to_string();
            // Find any libonnxruntime.so* in that directory
            if let Ok(entries) = std::fs::read_dir(&capi_dir) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let name_str = name.to_string_lossy();
                    if name_str.starts_with("libonnxruntime.so") && name_str != "libonnxruntime_providers_shared.so" {
                        return Some(entry.path());
                    }
                }
            }
        }
    }

    // 2. Check alongside the executable
    if let Ok(exe) = std::env::current_exe() {
        let candidate = exe.parent().unwrap().join("libonnxruntime.so");
        if candidate.exists() {
            return Some(candidate);
        }
    }

    None
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info,ort=warn")),
        )
        .init();
    info!("Starting io_oreilles service");

    // Auto-detect ORT_DYLIB_PATH if not already set (required for ort load-dynamic)
    if std::env::var("ORT_DYLIB_PATH").is_err() {
        if let Some(path) = find_ort_dylib() {
            info!("Auto-detected ORT_DYLIB_PATH: {}", path.display());
            // SAFETY: called before any threads are spawned
            unsafe { std::env::set_var("ORT_DYLIB_PATH", &path) };
        } else {
            tracing::warn!("ORT_DYLIB_PATH not set and libonnxruntime.so not found. Install onnxruntime via pip or set ORT_DYLIB_PATH manually.");
        }
    }

    // 1. Setup NATS connection
    let nats_url = std::env::var("NATS_URL").unwrap_or_else(|_| "nats://localhost:4222".to_string());
    let client = async_nats::connect(&nats_url).await.context("Failed to connect to NATS")?;
    info!("Connected to NATS");

    // 2. Setup Thread 2 -> Thread 3 Queue
    let (tx_speech, mut rx_speech) = mpsc::channel::<Vec<f32>>(32);

    // 3. Setup Ringbuf for Thread 1 -> Thread 2
    // Arbitrary size: 160_000 samples is 10s at 16kHz, roughly 3.3s at 48kHz.
    let rb = ringbuf::HeapRb::<f32>::new(160_000);
    let (mut prod, mut cons) = rb.split();

    // 4. Setup Thread 1 (OS Audio Callback via CPAL)
    let host = cpal::default_host();
    let device = host.default_input_device().context("No default input device available")?;
    info!("Input device: {}", device.name()?);

    let config = device.default_input_config()?;
    let sample_rate = config.sample_rate().0;
    let channels = config.channels();
    info!("Input config: {} Hz, {} channels", sample_rate, channels);

    let err_fn = |err| error!("an error occurred on stream: {}", err);

    let stream = match config.sample_format() {
        cpal::SampleFormat::F32 => {
            device.build_input_stream(
                &config.into(),
                move |data: &[f32], _: &cpal::InputCallbackInfo| {
                    let mono_data: Vec<f32> = data.iter().step_by(channels as usize).copied().collect();
                    let _ = prod.push_slice(&mono_data);
                },
                err_fn,
                None, // optional timeout
            )?
        }
        cpal::SampleFormat::I16 => {
            device.build_input_stream(
                &config.into(),
                move |data: &[i16], _: &cpal::InputCallbackInfo| {
                    let mono_data: Vec<f32> = data.iter().step_by(channels as usize).map(|&s| s.to_sample::<f32>()).collect();
                    prod.push_slice(&mono_data);
                },
                err_fn,
                None,
            )?
        }
        cpal::SampleFormat::U16 => {
             device.build_input_stream(
                &config.into(),
                move |data: &[u16], _: &cpal::InputCallbackInfo| {
                    let mono_data: Vec<f32> = data.iter().step_by(channels as usize).map(|&s| s.to_sample::<f32>()).collect();
                    prod.push_slice(&mono_data);
                },
                err_fn,
                None,
            )?
        }
        _ => anyhow::bail!("Unsupported sample format"),
    };

    stream.play()?;

    info!("Initializing Silero VAD Model...");
    use silero::{Session, StreamState, SampleRate as SileroSampleRate};
    let mut session = Session::bundled().context("Failed to load Silero VAD model. Is ORT_DYLIB_PATH set? See README.")?;
    let mut stream_state = StreamState::new(SileroSampleRate::Rate16k);
    info!("Silero VAD Model loaded.");

    // 5. Setup Thread 2 (VAD Watcher)
    std::thread::spawn(move || {
        info!("Started VAD Thread (Thread 2)");
        
        let target_sr = 16_000;
        let mut resampler = if sample_rate != target_sr {
            let params = SincInterpolationParameters {
                sinc_len: 256,
                f_cutoff: 0.95,
                interpolation: SincInterpolationType::Linear,
                oversampling_factor: 256,
                window: WindowFunction::BlackmanHarris2,
            };
            Some(SincFixedIn::<f32>::new(
                target_sr as f64 / sample_rate as f64,
                2.0,
                params,
                1024,
                1
            ).expect("Failed to init resampler"))
        } else {
            None
        };
        
        let mut speech_buffer: Vec<f32> = Vec::new();
        let mut is_speaking = false;
        let mut silence_frames = 0;
        let mut internal_buf = Vec::new();
        let mut vad_buf = Vec::new();

        // Diagnostic counters
        let mut diag_samples_read: u64 = 0;
        let mut diag_samples_16k: u64 = 0;
        let mut diag_vad_frames: u64 = 0;
        let mut diag_max_prob: f32 = 0.0;
        let mut diag_max_amp: f32 = 0.0;
        let mut diag_last = std::time::Instant::now();

        loop {
            // Read from ringbuf
            let mut chunk = vec![0.0; 4096];
            let read = cons.pop_slice(&mut chunk);
            if read == 0 {
                std::thread::sleep(std::time::Duration::from_millis(5));
                // Still print diagnostics even when idle
                if diag_last.elapsed() > std::time::Duration::from_secs(2) {
                    info!(
                        "[DIAG] ringbuf_read={}, resampled_16k={}, vad_frames={}, max_prob={:.3}, max_amp={:.5}",
                        diag_samples_read, diag_samples_16k, diag_vad_frames, diag_max_prob, diag_max_amp
                    );
                    diag_samples_read = 0;
                    diag_samples_16k = 0;
                    diag_vad_frames = 0;
                    diag_max_prob = 0.0;
                    diag_max_amp = 0.0;
                    diag_last = std::time::Instant::now();
                }
                continue;
            }
            chunk.truncate(read);
            diag_samples_read += read as u64;
            let amp = chunk.iter().fold(0.0_f32, |m, x| m.max(x.abs()));
            if amp > diag_max_amp { diag_max_amp = amp; }
            internal_buf.extend(&chunk);

            // Resample logic
            let chunk_16k = if let Some(r) = &mut resampler {
                let mut required_in = r.input_frames_next();
                let mut out_16k = Vec::new();
                while internal_buf.len() >= required_in {
                    let to_process = internal_buf.drain(0..required_in).collect::<Vec<_>>();
                    let waves_in = vec![to_process];
                    match r.process(&waves_in, None) {
                        Ok(mut out) => out_16k.extend(out.pop().unwrap()),
                        Err(e) => error!("Resampling error: {}", e),
                    }
                    required_in = r.input_frames_next();
                }
                out_16k
            } else {
                let out = internal_buf.clone();
                internal_buf.clear();
                out
            };

            diag_samples_16k += chunk_16k.len() as u64;
            vad_buf.extend(chunk_16k);

            // Silero expects chunks of length 512 (or 1536).
            while vad_buf.len() >= 512 {
                let frame: Vec<f32> = vad_buf.drain(0..512).collect();
                
                match session.infer_chunk(&mut stream_state, &frame) {
                    Ok(prob) => {
                        diag_vad_frames += 1;
                        if prob > diag_max_prob { diag_max_prob = prob; }
                        if prob > 0.5 {
                            if !is_speaking {
                                info!("Speech started (prob: {:.2})", prob);
                            }
                            is_speaking = true;
                            silence_frames = 0;
                            speech_buffer.extend_from_slice(&frame);
                        } else {
                            if is_speaking {
                                speech_buffer.extend_from_slice(&frame);
                                silence_frames += 1;
                                if silence_frames > 20 { // ~600ms of 30ms frames
                                    info!("Speech ended. Captured {} samples.", speech_buffer.len());
                                    is_speaking = false;
                                    tx_speech.blocking_send(speech_buffer.clone()).unwrap();
                                    speech_buffer.clear();
                                }
                            }
                        }
                    }
                    Err(e) => error!("VAD error: {:?}", e),
                }
            }

            // Periodic diagnostic output
            if diag_last.elapsed() > std::time::Duration::from_secs(2) {
                info!(
                    "[DIAG] ringbuf_read={}, resampled_16k={}, vad_frames={}, max_prob={:.3}, max_amp={:.5}",
                    diag_samples_read, diag_samples_16k, diag_vad_frames, diag_max_prob, diag_max_amp
                );
                diag_samples_read = 0;
                diag_samples_16k = 0;
                diag_vad_frames = 0;
                diag_max_prob = 0.0;
                diag_max_amp = 0.0;
                diag_last = std::time::Instant::now();
            }
        }
    });

    // 6. Thread 3 (STT) inside Tokio Runtime
    info!("Loading STT model...");
    // Initialisation propre d'une structure avec mise à jour des champs qui nous intéressent
    let whisper_config = ct2rs::Config {
        // 1. Le Métal
        device: ct2rs::Device::CPU,
        
        // 2. Le Moteur Mathématique
        // Laisse-le en Auto ! Le moteur va charger tes poids en Int8 depuis le disque, 
        // et fera les activations matricielles en Float32 car ton Ryzen n'a pas les 
        // instructions matérielles pour le Float16. C'est l'optimisation parfaite.
        compute_type: ct2rs::ComputeType::AUTO, 
        
        // 3. Le Cerveau (L'ex-intra_threads)
        // C'EST ICI QUE TOUT SE JOUE. On alloue 4 threads. Pourquoi pas 16 ? 
        // Parce qu'au-delà de 4 threads sur de l'audio temps réel, le coût de 
        // synchronisation entre les cœurs (Context Switching) devient plus lourd 
        // que le calcul lui-même. 4 cœurs vont pulvériser l'inférence.
        num_threads_per_replica: 4, 
        
        // 4. Parallélisme Tensoriel
        // On met 'false'. C'est une technologie de data-center pour couper 
        // un modèle de 70 Milliards de paramètres sur 4 ou 8 grosses cartes graphiques.
        tensor_parallel: false,
        
        // 5. Indexation Matérielle
        // On pointe vers le premier périphérique (ton CPU unique).
        device_indices: vec![0],
        
        // 6. Gestion Asynchrone Interne
        // 0 signifie que l'on utilise le comportement par défaut de la lib C++.
        max_queued_batches: 0,
        
        // 7. Affinité CPU (Pinning)
        // -1 indique qu'on laisse l'ordonnanceur (Scheduler) de ton OS Linux/Windows 
        // décider sur quels cœurs physiques envoyer le travail. C'est plus sûr sur un PC portable.
        cpu_core_offset: -1,
    };
    let whisper = ct2rs::Whisper::new("model/whisper-small-int8-ct2", whisper_config).unwrap();
    let whisper_options = ct2rs::WhisperOptions::default();
    let stt_language: Option<String> = std::env::var("STT_LANGUAGE").ok();
    
    info!("STT ready (language: {}). Waiting for speech events...", 
        stt_language.as_deref().unwrap_or("auto-detect"));
    while let Some(speech) = rx_speech.recv().await {
        info!("Received speech chunk of len {}", speech.len());
        let start = std::time::Instant::now();
        let lang = stt_language.as_deref();
        match whisper.generate(&speech, lang, false, &whisper_options) {
            Ok(result) => {
                let text = result.join(" ");
                info!("Transcribed in {:?}: {}", start.elapsed(), text);
                
                let event = TranscriptionEvent { text: text.clone() };
                let payload = serde_json::to_vec(&event).unwrap();
                client.publish("io.user.speak".to_string(), payload.into()).await.unwrap();
            }
            Err(e) => {
                error!("Whisper transcription failed: {}. Try setting STT_LANGUAGE=fr (or en, etc.)", e);
            }
        }
    }

    Ok(())
}
