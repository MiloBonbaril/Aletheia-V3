//! Per-speaker VAD pipeline for Discord voice (`--discord` mode): each speaker gets
//! its own resampler + Silero stream state + `VadSegmenter`, so one person going
//! quiet never cuts off another person mid-sentence. The Silero `Session` (the
//! loaded ONNX model) is reused across all speakers — only `StreamState` is
//! per-speaker — since `Session::infer_chunk` takes the stream state externally.

use rubato::{
    Resampler, SincFixedIn, SincInterpolationParameters, SincInterpolationType, WindowFunction,
};
use serde::Deserialize;
use silero::{SampleRate, Session, StreamState};

use crate::segmenter::{FrameOutcome, VadSegmenter};

const TARGET_SAMPLE_RATE: f64 = 16_000.0;
const SOURCE_SAMPLE_RATE: f64 = 48_000.0;

#[derive(Deserialize)]
pub struct DiscordVoiceFrame {
    pub speaker_id: String,
    pub speaker_name: String,
    pub pcm: String, // base64, s16le, 48kHz, stereo
}

/// Downmix interleaved 48kHz stereo s16le PCM to mono f32 samples in [-1, 1].
pub fn stereo_i16_to_mono_f32(pcm: &[u8]) -> Vec<f32> {
    pcm.chunks_exact(4)
        .map(|frame| {
            let l = i16::from_le_bytes([frame[0], frame[1]]) as f32;
            let r = i16::from_le_bytes([frame[2], frame[3]]) as f32;
            (l + r) / 2.0 / 32768.0
        })
        .collect()
}

fn new_resampler() -> SincFixedIn<f32> {
    let params = SincInterpolationParameters {
        sinc_len: 256,
        f_cutoff: 0.95,
        interpolation: SincInterpolationType::Linear,
        oversampling_factor: 256,
        window: WindowFunction::BlackmanHarris2,
    };
    SincFixedIn::<f32>::new(TARGET_SAMPLE_RATE / SOURCE_SAMPLE_RATE, 2.0, params, 1024, 1)
        .expect("Failed to init per-speaker resampler")
}

pub struct SpeakerPipeline {
    resampler: SincFixedIn<f32>,
    internal_buf: Vec<f32>,
    vad_buf: Vec<f32>,
    stream_state: StreamState,
    segmenter: VadSegmenter,
}

impl SpeakerPipeline {
    pub fn new() -> Self {
        Self {
            resampler: new_resampler(),
            internal_buf: Vec::new(),
            vad_buf: Vec::new(),
            stream_state: StreamState::new(SampleRate::Rate16k),
            segmenter: VadSegmenter::new(),
        }
    }

    /// Feed a chunk of mono 48kHz samples; returns any speech segments (16kHz f32)
    /// that ended as a result (normally 0 or 1 per call).
    pub fn push_mono_48k(&mut self, session: &mut Session, samples: &[f32]) -> Vec<Vec<f32>> {
        self.internal_buf.extend_from_slice(samples);
        let mut ended = Vec::new();

        let mut required_in = self.resampler.input_frames_next();
        while self.internal_buf.len() >= required_in {
            let to_process: Vec<f32> = self.internal_buf.drain(0..required_in).collect();
            match self.resampler.process(&[to_process], None) {
                Ok(mut out) => self.vad_buf.extend(out.pop().unwrap()),
                Err(e) => tracing::error!("Per-speaker resampling error: {}", e),
            }
            required_in = self.resampler.input_frames_next();
        }

        while self.vad_buf.len() >= 512 {
            let frame: Vec<f32> = self.vad_buf.drain(0..512).collect();
            match session.infer_chunk(&mut self.stream_state, &frame) {
                Ok(prob) => {
                    if let FrameOutcome::SpeechEnded(segment) =
                        self.segmenter.push_frame(&frame, prob)
                    {
                        ended.push(segment);
                    }
                }
                Err(e) => tracing::error!("Discord-speaker VAD error: {:?}", e),
            }
        }

        ended
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn downmixes_stereo_silence_to_zero() {
        let pcm = vec![0u8; 16]; // 4 stereo frames of silence
        assert_eq!(stereo_i16_to_mono_f32(&pcm), vec![0.0; 4]);
    }

    #[test]
    fn downmixes_stereo_full_scale_and_averages_channels() {
        let mut pcm = Vec::new();
        pcm.extend_from_slice(&i16::MAX.to_le_bytes()); // left, full scale
        pcm.extend_from_slice(&0i16.to_le_bytes()); // right, silence
        let mono = stereo_i16_to_mono_f32(&pcm);
        assert_eq!(mono.len(), 1);
        assert!((mono[0] - 0.5).abs() < 0.001);
    }
}
