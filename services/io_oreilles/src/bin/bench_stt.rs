//! Mesure la latence de `whisper.generate` sur un corpus de WAV 16 kHz mono.
//!
//! Sert à comparer CPU vs GPU sur la même machine, le même modèle et les mêmes
//! segments — les segments de `--dir` sont des extraits réels du pipeline
//! (exports hippocampe), pas de l'audio de synthèse.
//!
//! ```bash
//! cargo run --release --bin bench_stt -- --dir /chemin/vers/wavs --device cpu
//! cargo run --release --features cuda --bin bench_stt -- --dir ... --device cuda
//! ```
use anyhow::{Context, Result};
use std::path::PathBuf;
use std::time::Instant;

/// Lit un WAV PCM 16 bits mono et le normalise dans [-1, 1], comme
/// `create_wav_data` le produit côté io_oreilles (l'inverse exact).
fn read_wav_mono_f32(path: &PathBuf) -> Result<Vec<f32>> {
    let bytes = std::fs::read(path)?;
    anyhow::ensure!(bytes.len() > 44, "WAV trop court: {}", path.display());
    // ponytail: en-tête RIFF de taille fixe (44 octets), tel qu'écrit par
    // create_wav_data — pas de parseur de chunks, ce bench ne lit que nos WAV.
    Ok(bytes[44..]
        .chunks_exact(2)
        .map(|c| i16::from_le_bytes([c[0], c[1]]) as f32 / 32768.0)
        .collect())
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((sorted.len() - 1) as f64 * p).round() as usize;
    sorted[idx]
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let arg = |name: &str| -> Option<String> {
        args.iter()
            .position(|a| a == name)
            .and_then(|i| args.get(i + 1))
            .cloned()
    };

    let dir = PathBuf::from(arg("--dir").context("--dir <répertoire de WAV> requis")?);
    let device = arg("--device").unwrap_or_else(|| "cpu".to_string());
    let repeats: usize = arg("--repeats").and_then(|v| v.parse().ok()).unwrap_or(3);
    let model = arg("--model").unwrap_or_else(|| "model/whisper-large-turbo-ct2".to_string());

    let mut files: Vec<PathBuf> = std::fs::read_dir(&dir)?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|e| e == "wav"))
        .collect();
    files.sort();
    anyhow::ensure!(!files.is_empty(), "aucun .wav dans {}", dir.display());

    // `Device::CUDA` n'a de sens que si CTranslate2 a été compilé avec CUDA :
    // demander --device cuda sur un build CPU échoue franchement plutôt que de
    // retomber silencieusement sur le CPU et fausser la comparaison.
    let ct2_device = match device.as_str() {
        #[cfg(feature = "cuda")]
        "cuda" => ct2rs::Device::CUDA,
        #[cfg(not(feature = "cuda"))]
        "cuda" => anyhow::bail!("build sans CUDA : recompiler avec --features cuda"),
        _ => ct2rs::Device::CPU,
    };

    // Le service tourne en int8_float16 (cf. src/main.rs) : c'est le défaut ici aussi.
    // `--compute auto` sert à remesurer l'ancienne base float16 sur le même corpus,
    // sinon la comparaison small/turbo mélangerait deux changements à la fois.
    let compute = arg("--compute").unwrap_or_else(|| "int8_float16".to_string());
    let ct2_compute = match compute.as_str() {
        "auto" => ct2rs::ComputeType::AUTO,
        "float16" => ct2rs::ComputeType::FLOAT16,
        "int8" => ct2rs::ComputeType::INT8,
        "int8_float16" => ct2rs::ComputeType::INT8_FLOAT16,
        other => anyhow::bail!("--compute inconnu: {other}"),
    };

    // Mêmes réglages que le service (cf. src/main.rs) pour que le chiffre mesuré
    // soit celui qu'on vivra en production, pas celui d'une config de bench.
    let config = ct2rs::Config {
        device: ct2_device,
        compute_type: ct2_compute,
        num_threads_per_replica: arg("--threads").and_then(|v| v.parse().ok()).unwrap_or(16),
        tensor_parallel: false,
        device_indices: vec![0],
        max_queued_batches: 0,
        cpu_core_offset: -1,
    };

    let t_load = Instant::now();
    let whisper = ct2rs::Whisper::new(&model, config)?;
    let load_ms = t_load.elapsed().as_secs_f64() * 1000.0;

    let options = ct2rs::WhisperOptions::default();
    // Même résolution que le service (cf. src/main.rs) : le bench doit mesurer ce que la
    // production vit. Mesurer en détection automatique pendant que le service tourne en `fr`
    // est exactement ce qui a caché 73 ms pendant des mois.
    let lang: Option<String> = match std::env::var("STT_LANGUAGE").as_deref() {
        Ok("auto") => None,
        Ok(l) if !l.is_empty() => Some(l.to_string()),
        _ => Some("fr".to_string()),
    };

    println!(
        "device={device} compute={compute} langue={} model={model} fichiers={} répétitions={repeats}",
        lang.as_deref().unwrap_or("auto"),
        files.len()
    );
    println!("chargement du modèle: {load_ms:.0} ms\n");

    // Warm-up : première inférence (alloc CUDA, caches) exclue des stats.
    let warm = read_wav_mono_f32(&files[0])?;
    let t_warm = Instant::now();
    let _ = whisper.generate(&warm, lang.as_deref(), false, &options)?;
    println!("warm-up: {:.0} ms\n", t_warm.elapsed().as_secs_f64() * 1000.0);

    let mut all_ms: Vec<f64> = Vec::new();
    let mut total_audio_s = 0.0;

    for file in &files {
        let samples = read_wav_mono_f32(file)?;
        let audio_s = samples.len() as f64 / 16000.0;
        let mut runs = Vec::with_capacity(repeats);
        let mut text = String::new();

        for _ in 0..repeats {
            let t = Instant::now();
            let res = whisper.generate(&samples, lang.as_deref(), false, &options)?;
            runs.push(t.elapsed().as_secs_f64() * 1000.0);
            text = res.join(" ");
        }

        runs.sort_by(f64::total_cmp);
        let median = runs[runs.len() / 2];
        all_ms.push(median);
        total_audio_s += audio_s;

        let name = file.file_name().unwrap_or_default().to_string_lossy();
        println!(
            "{name:<22} audio={audio_s:>5.1}s  médiane={median:>7.1}ms  rtf={:>5.2}x  {}",
            median / 1000.0 / audio_s,
            text.trim().chars().take(60).collect::<String>()
        );
    }

    all_ms.sort_by(f64::total_cmp);
    let sum: f64 = all_ms.iter().sum();
    println!("\n── {device} ──");
    println!("segments      : {}", all_ms.len());
    println!("audio total   : {total_audio_s:.1} s");
    println!("médiane       : {:.1} ms", percentile(&all_ms, 0.50));
    println!("p90           : {:.1} ms", percentile(&all_ms, 0.90));
    println!("max           : {:.1} ms", all_ms[all_ms.len() - 1]);
    println!("moyenne       : {:.1} ms", sum / all_ms.len() as f64);
    println!("rtf moyen     : {:.3}x", (sum / 1000.0) / total_audio_s);
    Ok(())
}
