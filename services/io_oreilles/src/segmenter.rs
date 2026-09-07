//! Speech/silence accumulation, extracted from the mic-capture thread so it can be
//! driven by any audio source (one `VadSegmenter` instance per source).

const SPEECH_PROB_THRESHOLD: f32 = 0.5;
const SILENCE_FRAMES_TO_END: u32 = 20; // ~600ms of 512-sample @16kHz frames
/// A segment carrying less speech than this never reaches Whisper. One frame above the
/// threshold used to open a segment, and Whisper always returns *something*: a 160ms cough
/// came back as "Merci d'avoir regardé cette vidéo !", a 224ms one as "Salut !". `main.rs`
/// published those, since the only filter was an empty string — so the entity answered a
/// throat clear out loud and wrote the exchange to memory.
/// Silero itself rejects silence, white noise and clicks (prob <= 0.10), so the trigger is
/// specifically a *speech-shaped* sound too short to be a word: a cough, an "ah", one word
/// from someone else in the room.
/// 8 frames is ~256ms; the shortest real word in the bench corpus ("Bon.") carries 13.
/// See docs/audits/io_oreilles-2026-09-07.md, F1.
const MIN_SPEECH_FRAMES: u32 = 8;
/// Hard ceiling on one chunk of audio handed to Whisper. ct2rs splits anything longer into
/// one 30s window per 30s of audio and runs them as one batch (`ct2rs` whisper.rs:101), so an
/// uncapped buffer costs unbounded VRAM and latency, and loses text at every window joint —
/// measured 2306 MiB and 604 ms on 95s of speech. 25s leaves room for the ~672ms silence tail
/// inside a single window. See docs/audits/io_oreilles-2026-09-07.md, F2.
const MAX_CHUNK_SAMPLES: usize = 25 * 16_000;
/// A forced cut prefers to land in a pause, so no word is split and no audio is repeated.
/// The pause must be recent enough that the emitted chunk is not far below the ceiling.
const MIN_CHUNK_SAMPLES: usize = 15 * 16_000;
/// Fallback when the speaker never paused: cut flat, and carry this much audio into the next
/// chunk so the split word appears whole in it.
const HARD_CUT_OVERLAP_SAMPLES: usize = 16_000 / 2; // 500 ms

/// What happened when a scored frame was pushed into the segmenter.
pub enum FrameOutcome {
    Idle,
    SpeechStarted,
    Speaking,
    /// The buffer hit `MAX_CHUNK_SAMPLES` and the speaker has **not** stopped. Transcribe this
    /// chunk now to hide its cost behind the ongoing speech, but hold the text: publishing it
    /// would make the entity answer half a sentence while the person is still talking.
    SpeechContinues(Vec<f32>),
    SpeechEnded(Vec<f32>),
    /// The segment ended, but it held too little speech to be worth transcribing.
    /// The frame count rides along so the logs can settle `MIN_SPEECH_FRAMES` on real audio.
    SpeechDiscarded { speech_frames: u32 },
}

pub struct VadSegmenter {
    speech_buffer: Vec<f32>,
    is_speaking: bool,
    silence_frames: u32,
    speech_frames: u32,
    /// Offset in `speech_buffer` just after the most recent silence frame: the preferred cut
    /// point. `None` until a pause appears in the current buffer.
    last_pause_at: Option<usize>,
    /// True once this utterance has already given a `SpeechContinues` chunk. It waives the
    /// `MIN_SPEECH_FRAMES` rule on the final chunk: the tail of a long sentence is short by
    /// nature, and dropping it would also drop the text held for the whole utterance.
    emitted_partial: bool,
}

impl VadSegmenter {
    pub fn new() -> Self {
        Self {
            speech_buffer: Vec::new(),
            is_speaking: false,
            silence_frames: 0,
            speech_frames: 0,
            last_pause_at: None,
            emitted_partial: false,
        }
    }

    /// Split the buffer at the ceiling and keep the remainder for the next chunk.
    fn force_cut(&mut self) -> FrameOutcome {
        let cut = match self.last_pause_at {
            // Cut inside the pause: the chunk ends on silence and the next one starts on a
            // word boundary, so nothing is split and nothing is repeated.
            Some(at) if at >= MIN_CHUNK_SAMPLES => at,
            // ponytail: pas de déduplication du recouvrement. Le mot coupé est entier dans le
            // morceau suivant, mais sa moitié reste à la fin du précédent : la jointure lit
            // « la synthè synthèse vocale ». Chemin rare (il faut 15 s sans une seule pause
            // détectée). Si ça se voit, aligner le suffixe du morceau N sur le préfixe de N+1.
            _ => self.speech_buffer.len() - HARD_CUT_OVERLAP_SAMPLES,
        };
        let rest = self.speech_buffer.split_off(cut);
        let chunk = std::mem::replace(&mut self.speech_buffer, rest);
        self.last_pause_at = None;
        self.speech_frames = 0;
        self.emitted_partial = true;
        FrameOutcome::SpeechContinues(chunk)
    }

    /// Feed one VAD-scored frame (its probability of containing speech).
    pub fn push_frame(&mut self, frame: &[f32], prob: f32) -> FrameOutcome {
        if prob > SPEECH_PROB_THRESHOLD {
            let just_started = !self.is_speaking;
            self.is_speaking = true;
            self.silence_frames = 0;
            self.speech_frames += 1;
            self.speech_buffer.extend_from_slice(frame);
            if self.speech_buffer.len() >= MAX_CHUNK_SAMPLES {
                // Only the speech branch checks the ceiling. Crossing it during the silence
                // tail would carve off a chunk that the natural end closes 672ms later anyway,
                // and the buffer still stays under 30s (25s + 512 + 21 frames = 25.7s).
                self.force_cut()
            } else if just_started {
                FrameOutcome::SpeechStarted
            } else {
                FrameOutcome::Speaking
            }
        } else if self.is_speaking {
            self.speech_buffer.extend_from_slice(frame);
            self.silence_frames += 1;
            self.last_pause_at = Some(self.speech_buffer.len());
            if self.silence_frames > SILENCE_FRAMES_TO_END {
                self.is_speaking = false;
                let segment = std::mem::take(&mut self.speech_buffer);
                let speech_frames = std::mem::take(&mut self.speech_frames);
                let emitted_partial = std::mem::take(&mut self.emitted_partial);
                self.last_pause_at = None;
                if speech_frames < MIN_SPEECH_FRAMES && !emitted_partial {
                    // ponytail: on compte les trames de parole, pas la longueur du segment.
                    // Une pause au milieu d'un mot gonfle le buffer sans gonfler ce compteur,
                    // donc un "oui" hésitant reste jugé sur sa parole réelle.
                    FrameOutcome::SpeechDiscarded { speech_frames }
                } else {
                    FrameOutcome::SpeechEnded(segment)
                }
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

    /// Pousse `speech` trames de parole puis assez de silence pour fermer le segment.
    fn utterance(seg: &mut VadSegmenter, speech: u32) -> FrameOutcome {
        for _ in 0..speech {
            seg.push_frame(&frame(1.0), 0.9);
        }
        for _ in 0..SILENCE_FRAMES_TO_END {
            seg.push_frame(&frame(0.0), 0.1);
        }
        seg.push_frame(&frame(0.0), 0.1)
    }

    #[test]
    fn sustained_silence_ends_utterance_with_full_buffer() {
        let mut seg = VadSegmenter::new();
        let speech = MIN_SPEECH_FRAMES;
        match utterance(&mut seg, speech) {
            // La queue de silence reste dans le segment : Whisper la remplit jusqu'à 30 s de
            // toute façon, et la couper coûterait la fin du dernier mot.
            FrameOutcome::SpeechEnded(segment) => assert_eq!(
                segment.len(),
                512 * (speech as usize + SILENCE_FRAMES_TO_END as usize + 1)
            ),
            _ => panic!("expected SpeechEnded"),
        }
    }

    #[test]
    fn buffer_clears_after_ending_and_is_idle_again() {
        let mut seg = VadSegmenter::new();
        assert!(matches!(utterance(&mut seg, MIN_SPEECH_FRAMES), FrameOutcome::SpeechEnded(_)));
        assert!(matches!(seg.push_frame(&frame(0.0), 0.1), FrameOutcome::Idle));
    }

    #[test]
    fn a_burst_too_short_to_be_a_word_is_discarded() {
        // Une toux, un raclement de gorge : Silero les score à 1.00, le segment s'ouvre.
        // Sans ce filtre, Whisper renvoie "Merci." ou "Salut !" et le cortex y répond.
        let mut seg = VadSegmenter::new();
        assert!(matches!(
            utterance(&mut seg, 1),
            FrameOutcome::SpeechDiscarded { speech_frames: 1 }
        ));
    }

    #[test]
    fn the_threshold_keeps_the_shortest_real_word() {
        let mut seg = VadSegmenter::new();
        assert!(matches!(
            utterance(&mut seg, MIN_SPEECH_FRAMES - 1),
            FrameOutcome::SpeechDiscarded { .. }
        ));
        // 13 trames : "Bon." du corpus de bench, la parole réelle la plus courte mesurée.
        let mut seg = VadSegmenter::new();
        assert!(matches!(utterance(&mut seg, 13), FrameOutcome::SpeechEnded(_)));
    }

    #[test]
    fn a_discard_leaves_no_audio_behind_for_the_next_utterance() {
        let mut seg = VadSegmenter::new();
        assert!(matches!(utterance(&mut seg, 1), FrameOutcome::SpeechDiscarded { .. }));
        let speech = MIN_SPEECH_FRAMES;
        match utterance(&mut seg, speech) {
            FrameOutcome::SpeechEnded(segment) => assert_eq!(
                segment.len(),
                512 * (speech as usize + SILENCE_FRAMES_TO_END as usize + 1)
            ),
            _ => panic!("le segment rejeté a pollué le suivant"),
        }
    }

    /// Pousse `n` trames de parole et rend les morceaux que la coupe forcée a produits.
    fn speak(seg: &mut VadSegmenter, n: usize) -> Vec<Vec<f32>> {
        let mut chunks = Vec::new();
        for _ in 0..n {
            if let FrameOutcome::SpeechContinues(c) = seg.push_frame(&frame(1.0), 0.9) {
                chunks.push(c);
            }
        }
        chunks
    }

    fn pause(seg: &mut VadSegmenter, n: usize) {
        for _ in 0..n {
            seg.push_frame(&frame(0.0), 0.1);
        }
    }

    /// Trames de parole nécessaires pour atteindre le plafond depuis un tampon vide.
    const FRAMES_TO_CEILING: usize = MAX_CHUNK_SAMPLES.div_ceil(512);

    #[test]
    fn a_long_turn_is_cut_instead_of_growing_without_a_limit() {
        let mut seg = VadSegmenter::new();
        let chunks = speak(&mut seg, FRAMES_TO_CEILING);
        assert_eq!(chunks.len(), 1, "le plafond doit produire exactement un morceau");
        assert!(chunks[0].len() <= MAX_CHUNK_SAMPLES);
    }

    #[test]
    fn no_chunk_ever_reaches_the_30s_whisper_window() {
        // 90 s de parole d'affilée, sans une seule pause : le pire cas.
        const WINDOW: usize = 30 * 16_000;
        let mut seg = VadSegmenter::new();
        let mut chunks = speak(&mut seg, 90 * 16_000 / 512);
        pause(&mut seg, SILENCE_FRAMES_TO_END as usize);
        match seg.push_frame(&frame(0.0), 0.1) {
            FrameOutcome::SpeechEnded(last) => chunks.push(last),
            _ => panic!("expected SpeechEnded"),
        }
        assert!(chunks.len() >= 3, "90 s doivent donner 3 morceaux ou plus");
        for c in &chunks {
            assert!(c.len() < WINDOW, "morceau de {} échantillons >= 30 s", c.len());
        }
    }

    #[test]
    fn a_forced_cut_lands_in_the_last_pause() {
        let mut seg = VadSegmenter::new();
        let before = 20 * 16_000 / 512; // ~20 s, au-dessus de MIN_CHUNK_SAMPLES
        speak(&mut seg, before);
        pause(&mut seg, 2); // une respiration : la coupe doit tomber ici
        let cut_at = (before + 2) * 512;
        let chunks = speak(&mut seg, FRAMES_TO_CEILING);
        assert_eq!(chunks[0].len(), cut_at, "la coupe doit suivre la dernière pause");
    }

    #[test]
    fn a_forced_cut_with_no_pause_carries_the_overlap_forward() {
        // Sans pause, le mot coupé doit se retrouver entier dans le morceau suivant.
        let mut seg = VadSegmenter::new();
        let chunks = speak(&mut seg, FRAMES_TO_CEILING);
        let emitted = chunks[0].len();
        pause(&mut seg, SILENCE_FRAMES_TO_END as usize);
        match seg.push_frame(&frame(0.0), 0.1) {
            FrameOutcome::SpeechEnded(rest) => {
                let kept = rest.len() - 512 * (SILENCE_FRAMES_TO_END as usize + 1);
                assert_eq!(kept, HARD_CUT_OVERLAP_SAMPLES);
                assert_eq!(emitted + kept, FRAMES_TO_CEILING * 512);
            }
            _ => panic!("expected SpeechEnded"),
        }
    }

    #[test]
    fn the_tail_of_a_long_turn_is_never_discarded() {
        // Après une coupe forcée, la personne dit deux mots et se tait. Ce reste est court,
        // mais le rejeter perdrait aussi tout le texte retenu pour le tour.
        let mut seg = VadSegmenter::new();
        speak(&mut seg, FRAMES_TO_CEILING);
        speak(&mut seg, 1);
        pause(&mut seg, SILENCE_FRAMES_TO_END as usize);
        assert!(matches!(
            seg.push_frame(&frame(0.0), 0.1),
            FrameOutcome::SpeechEnded(_)
        ));
    }

    #[test]
    fn a_short_blip_after_a_finished_long_turn_is_discarded_again() {
        let mut seg = VadSegmenter::new();
        speak(&mut seg, FRAMES_TO_CEILING);
        pause(&mut seg, SILENCE_FRAMES_TO_END as usize);
        seg.push_frame(&frame(0.0), 0.1); // ferme le tour, remet emitted_partial à false
        assert!(matches!(
            utterance(&mut seg, 1),
            FrameOutcome::SpeechDiscarded { .. }
        ));
    }

    #[test]
    fn independent_instances_do_not_share_state() {
        let mut a = VadSegmenter::new();
        let mut b = VadSegmenter::new();
        assert!(matches!(a.push_frame(&frame(1.0), 0.9), FrameOutcome::SpeechStarted));
        assert!(matches!(b.push_frame(&frame(0.0), 0.1), FrameOutcome::Idle));
    }
}
