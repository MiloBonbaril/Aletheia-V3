//! Speech/silence accumulation, extracted from the mic-capture thread so it can be
//! driven by any audio source (one `VadSegmenter` instance per source).

const SPEECH_PROB_THRESHOLD: f32 = 0.5;
const SILENCE_FRAMES_TO_END: u32 = 20; // ~600ms of 512-sample @16kHz frames

/// What happened when a scored frame was pushed into the segmenter.
pub enum FrameOutcome {
    Idle,
    SpeechStarted,
    Speaking,
    SpeechEnded(Vec<f32>),
}

pub struct VadSegmenter {
    speech_buffer: Vec<f32>,
    is_speaking: bool,
    silence_frames: u32,
}

impl VadSegmenter {
    pub fn new() -> Self {
        Self {
            speech_buffer: Vec::new(),
            is_speaking: false,
            silence_frames: 0,
        }
    }

    /// Feed one VAD-scored frame (its probability of containing speech).
    pub fn push_frame(&mut self, frame: &[f32], prob: f32) -> FrameOutcome {
        if prob > SPEECH_PROB_THRESHOLD {
            let just_started = !self.is_speaking;
            self.is_speaking = true;
            self.silence_frames = 0;
            self.speech_buffer.extend_from_slice(frame);
            if just_started {
                FrameOutcome::SpeechStarted
            } else {
                FrameOutcome::Speaking
            }
        } else if self.is_speaking {
            self.speech_buffer.extend_from_slice(frame);
            self.silence_frames += 1;
            if self.silence_frames > SILENCE_FRAMES_TO_END {
                self.is_speaking = false;
                FrameOutcome::SpeechEnded(std::mem::take(&mut self.speech_buffer))
            } else {
                FrameOutcome::Speaking
            }
        } else {
            FrameOutcome::Idle
        }
    }
}

impl Default for VadSegmenter {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(fill: f32) -> Vec<f32> {
        vec![fill; 512]
    }

    #[test]
    fn stays_idle_on_silence() {
        let mut seg = VadSegmenter::new();
        for _ in 0..10 {
            assert!(matches!(seg.push_frame(&frame(0.0), 0.1), FrameOutcome::Idle));
        }
    }

    #[test]
    fn reports_speech_started_once_then_speaking() {
        let mut seg = VadSegmenter::new();
        assert!(matches!(seg.push_frame(&frame(1.0), 0.9), FrameOutcome::SpeechStarted));
        assert!(matches!(seg.push_frame(&frame(1.0), 0.9), FrameOutcome::Speaking));
    }

    #[test]
    fn brief_silence_does_not_end_utterance() {
        let mut seg = VadSegmenter::new();
        seg.push_frame(&frame(1.0), 0.9); // start
        for _ in 0..SILENCE_FRAMES_TO_END {
            assert!(matches!(seg.push_frame(&frame(0.0), 0.1), FrameOutcome::Speaking));
        }
        // Still speaking: recovers before the cutoff is exceeded.
        assert!(matches!(seg.push_frame(&frame(1.0), 0.9), FrameOutcome::Speaking));
    }

    #[test]
    fn sustained_silence_ends_utterance_with_full_buffer() {
        let mut seg = VadSegmenter::new();
        seg.push_frame(&frame(1.0), 0.9);
        seg.push_frame(&frame(1.0), 0.9);
        for _ in 0..SILENCE_FRAMES_TO_END {
            seg.push_frame(&frame(0.0), 0.1);
        }
        match seg.push_frame(&frame(0.0), 0.1) {
            FrameOutcome::SpeechEnded(segment) => assert_eq!(segment.len(), 512 * (2 + SILENCE_FRAMES_TO_END as usize + 1)),
            _ => panic!("expected SpeechEnded"),
        }
    }

    #[test]
    fn buffer_clears_after_ending_and_is_idle_again() {
        let mut seg = VadSegmenter::new();
        seg.push_frame(&frame(1.0), 0.9);
        for _ in 0..=SILENCE_FRAMES_TO_END {
            seg.push_frame(&frame(0.0), 0.1);
        }
        assert!(matches!(seg.push_frame(&frame(0.0), 0.1), FrameOutcome::Idle));
    }

    #[test]
    fn independent_instances_do_not_share_state() {
        let mut a = VadSegmenter::new();
        let mut b = VadSegmenter::new();
        assert!(matches!(a.push_frame(&frame(1.0), 0.9), FrameOutcome::SpeechStarted));
        assert!(matches!(b.push_frame(&frame(0.0), 0.1), FrameOutcome::Idle));
    }
}
