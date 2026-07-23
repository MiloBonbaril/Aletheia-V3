use async_nats::Client;
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use std::{borrow::Cow, sync::Arc, time::{SystemTime, UNIX_EPOCH}};
use tokio::sync::mpsc;
use tracing::{error, info, instrument, warn, Level};
use tracing_subscriber::FmtSubscriber;
use uuid::Uuid;

// --- External Payloads (Boundary Contracts) ---

#[derive(Serialize)]
struct PromptPayload {
    prompt: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    images: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    audio: Option<String>,
    correlation_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    source: Option<String>,
}

#[derive(Serialize)]
struct InteractionStartedPayload {
    correlation_id: String,
    source: String,
}

#[derive(Serialize)]
struct ContextBuildPayload {
    prompt: String,
    correlation_id: String,
    n_history: usize,
    skip_rag: bool,
}

#[derive(Deserialize, Debug)]
struct FragmentPayload<'a> {
    sequence: usize,
    #[serde(borrow)]
    text: Cow<'a, str>,
    is_last: bool,
}

#[derive(Deserialize, Debug)]
struct UserMsgPayload {
    text: String,
    #[serde(default)]
    images: Vec<String>,
}

#[derive(Deserialize, Debug)]
struct ProactiveTriggerPayload {
    prompt: String,
    #[serde(default)]
    source: Option<String>,
}

/// Étiquette de traçabilité pour `cortex.interaction.started` : "proactive" si le
/// dispatch vient de `limbic.proactive.trigger`, "user" pour toute autre origine.
fn interaction_source_label(source: &Option<String>) -> String {
    source.clone().unwrap_or_else(|| "user".to_string())
}

/// Text riding alongside a raw-audio `PromptInbound` so the LLM knows who it's
/// hearing (`io_oreilles --discord` mode tags each segment with a speaker name);
/// empty when the audio has no attached identity (local mic).
fn voice_prompt_text(speaker: &Option<String>) -> String {
    speaker
        .as_ref()
        .map(|name| format!("{} said (voice):", name))
        .unwrap_or_default()
}

/// Voice-originated prompts (raw audio attached) skip Hippocampe's RAG lookup —
/// the Qdrant/SentenceTransformers round-trip can't keep pace with real-time
/// voice conversation, and RAG is only ever useful for the text it's asked about.
fn should_skip_rag(audio: &Option<String>) -> bool {
    audio.is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn proactive_source_is_kept_as_is() {
        assert_eq!(interaction_source_label(&Some("proactive".to_string())), "proactive");
    }

    #[test]
    fn absent_source_defaults_to_user() {
        assert_eq!(interaction_source_label(&None), "user");
    }

    #[test]
    fn voice_prompt_text_includes_speaker_name() {
        assert_eq!(voice_prompt_text(&Some("Alice".to_string())), "Alice said (voice):");
    }

    #[test]
    fn voice_prompt_text_empty_without_speaker() {
        assert_eq!(voice_prompt_text(&None), "");
    }

    #[test]
    fn rag_skipped_when_audio_present() {
        assert!(should_skip_rag(&Some("base64...".to_string())));
    }

    #[test]
    fn rag_kept_when_no_audio() {
        assert!(!should_skip_rag(&None));
    }
}

// --- Internal Architectural Contracts (The Blueprint) ---

#[derive(Debug, Clone)]
pub struct EventEnvelope {
    pub correlation_id: Uuid,
    pub session_id: String,
    pub timestamp_ms: u128,
    pub payload: EventPayload,
}

#[derive(Debug, Clone)]
pub enum EventPayload {
    PromptInbound { text: String, images: Vec<String>, audio: Option<String>, source: Option<String> },
    LobeFragment { sequence: usize, text: String, is_last: bool },
    FatalSystemError(String),
}

pub struct SessionContext {
    pub start_time: u128,
    pub total_fragments: usize,
}

pub struct CortexDispatcher {
    pub active_sessions: Arc<dashmap::DashMap<String, SessionContext>>,
    pub nats: Client,
    pub ingestion_tx: mpsc::Sender<EventEnvelope>,
}

#[async_trait::async_trait]
pub trait NeuralRouter {
    async fn ingest_event(&self, event: EventEnvelope);
    async fn flush_session(&self, session_id: &str);
}

impl CortexDispatcher {
    pub fn new(nats: Client, ingestion_tx: mpsc::Sender<EventEnvelope>) -> Self {
        Self {
            active_sessions: Arc::new(dashmap::DashMap::new()),
            nats,
            ingestion_tx,
        }
    }

    #[instrument(skip(self, payload), fields(correlation_id = %correlation_id))]
    pub async fn process_envelope(&self, correlation_id: Uuid, session_id: &str, payload: EventPayload) {
        let timestamp_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();

        let event = EventEnvelope {
            correlation_id,
            session_id: session_id.to_string(),
            timestamp_ms,
            payload,
        };

        // Inject into the central nervous system (with backpressure via mpsc)
        if let Err(e) = self.ingestion_tx.send(event).await {
            error!("💀 FATAL: Central ingestion queue is completely saturated or dead: {}", e);
        }
    }
}

// --- Dispatcher Worker ---

async fn run_dispatcher_worker(
    mut rx: mpsc::Receiver<EventEnvelope>,
    nats: Client,
    active_sessions: Arc<dashmap::DashMap<String, SessionContext>>,
) {
    info!("🧠 Cortex Dispatcher (Actor) is online and ready for processing.");
    
    while let Some(event) = rx.recv().await {
        let span = tracing::info_span!(
            "dispatch",
            corr = %event.correlation_id,
            session = %event.session_id,
        );
        let _guard = span.enter();

        match event.payload {
            EventPayload::PromptInbound { text, images, audio, source } => {
                info!("📥 Routing inbound prompt (length: {}, images: {}, has_audio: {}, source: {:?})", text.len(), images.len(), audio.is_some(), source);
                let skip_rag = should_skip_rag(&audio);

                // Track session start
                active_sessions.insert(event.session_id.clone(), SessionContext {
                    start_time: event.timestamp_ms,
                    total_fragments: 0,
                });

                let corr_id = event.correlation_id.to_string();

                let prompt_msg = PromptPayload {
                    prompt: text.clone(),
                    images,
                    audio,
                    correlation_id: corr_id.clone(),
                    source: source.clone(),
                };

                let context_msg = ContextBuildPayload {
                    prompt: text,
                    correlation_id: corr_id,
                    n_history: 20,
                    skip_rag,
                };

                // Dispatch parallèle vers le Lobe Frontal ET l'Hippocampe
                let prompt_bytes = serde_json::to_vec(&prompt_msg);
                let context_bytes = serde_json::to_vec(&context_msg);

                match (prompt_bytes, context_bytes) {
                    (Ok(pb), Ok(cb)) => {
                        let (r1, r2) = tokio::join!(
                            nats.publish("cortex.prompt", pb.into()),
                            nats.publish("hippocampe.context.build", cb.into())
                        );
                        if let Err(e) = r1 { error!("⚠️ Failed to emit to Lobe Frontal: {}", e); }
                        if let Err(e) = r2 { error!("⚠️ Failed to emit to Hippocampe: {}", e); }
                        info!("📤 Prompt + Context build dispatched in parallel.");

                        // Signal léger : une interaction démarre, quelle que soit son origine.
                        let interaction_msg = InteractionStartedPayload {
                            correlation_id: event.correlation_id.to_string(),
                            source: interaction_source_label(&source),
                        };
                        match serde_json::to_vec(&interaction_msg) {
                            Ok(ib) => {
                                if let Err(e) = nats.publish("cortex.interaction.started", ib.into()).await {
                                    error!("⚠️ Failed to emit cortex.interaction.started: {}", e);
                                }
                            }
                            Err(e) => error!("⚠️ Serialization error (interaction.started): {}", e),
                        }
                    }
                    (Err(e), _) | (_, Err(e)) => {
                        error!("⚠️ Serialization error: {}", e);
                    }
                }
            }
            EventPayload::LobeFragment { sequence, text, is_last } => {
                // Update session state
                if let Some(mut ctx) = active_sessions.get_mut(&event.session_id) {
                    ctx.total_fragments += 1;
                }

                info!("🧩 Routed fragment n°{} (len: {})", sequence, text.len());

                if is_last {
                    info!("✅ Session {} complete (End of transmission).", event.session_id);
                    // In a more complex setup, we could flush caching or trigger other events here.
                    active_sessions.remove(&event.session_id);
                }
            }
            EventPayload::FatalSystemError(err) => {
                error!("💀 PANIC EVENT within the stream: {}", err);
            }
        }
    }
    
    warn!("Cortex Dispatcher worker loop terminated.");
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // 1. Initialiser le tracing (Observabilité industrielle)
    let subscriber = FmtSubscriber::builder()
        .with_max_level(Level::INFO)
        .finish();
    tracing::subscriber::set_global_default(subscriber).expect("tracing init failed");

    info!("🚀 Initializing Cortex Hub Runtime (Chaos Optimized)...");

    // 2. NATS Connection (Résilience implicite via loop interne)
    let nats_client = async_nats::connect("nats://localhost:4222").await?;
    info!("✅ Cortex connected to the NATS nervous system.");

    // 3. Backpressure & Rate Limiting (MPSC channel lock-free borné)
    let (tx, rx) = mpsc::channel::<EventEnvelope>(1000); 
    let active_sessions = Arc::new(dashmap::DashMap::new());

    let dispatcher = Arc::new(CortexDispatcher {
        active_sessions: active_sessions.clone(),
        nats: nats_client.clone(),
        ingestion_tx: tx,
    });

    // 4. Spawn Actor Dispatcher
    let nats_clone = nats_client.clone();
    let sessions_clone = active_sessions.clone();
    let dispatcher_handle = tokio::spawn(async move {
        run_dispatcher_worker(rx, nats_clone, sessions_clone).await;
    });

    // 5. Ingestion IO -> Dispatcher
    let mut user_msg_subscriber = nats_client.subscribe("io.user.msg.text").await?;
    let dispatcher_io = dispatcher.clone();
    let _io_handle = tokio::spawn(async move {
        info!("👂 Cortex Ingress Listening on 'io.user.msg.text'...");
        while let Some(msg) = user_msg_subscriber.next().await {
            let (prompt_text, images) = if let Ok(payload) = serde_json::from_slice::<UserMsgPayload>(&msg.payload) {
                (payload.text, payload.images)
            } else if let Ok(text_slice) = std::str::from_utf8(&msg.payload) {
                (text_slice.to_string(), vec![])
            } else {
                warn!("⚠️ Ingression Error: Unsupported message format.");
                continue;
            };

            let corr_id = Uuid::new_v4();
            let session_id = "global_session".to_string(); // Extension point: parser depuis metadata nats plus tard

            dispatcher_io.process_envelope(
                corr_id,
                &session_id,
                EventPayload::PromptInbound { text: prompt_text, images, audio: None, source: None }
            ).await;
        }
    });

    // 5a. Ingestion Déclencheur Proactif (Limbic) -> Dispatcher
    let mut proactive_subscriber = nats_client.subscribe("limbic.proactive.trigger").await?;
    let dispatcher_proactive = dispatcher.clone();
    let _proactive_handle = tokio::spawn(async move {
        info!("👂 Cortex Ingress Listening on 'limbic.proactive.trigger'...");
        while let Some(msg) = proactive_subscriber.next().await {
            let (prompt_text, trigger_source) = if let Ok(payload) = serde_json::from_slice::<ProactiveTriggerPayload>(&msg.payload) {
                (payload.prompt, payload.source)
            } else if let Ok(text_slice) = std::str::from_utf8(&msg.payload) {
                (text_slice.to_string(), None)
            } else {
                warn!("⚠️ Ingression Error: Unsupported proactive trigger format.");
                continue;
            };

            let corr_id = Uuid::new_v4();
            let session_id = "global_session".to_string();

            dispatcher_proactive.process_envelope(
                corr_id,
                &session_id,
                EventPayload::PromptInbound {
                    text: prompt_text,
                    images: vec![],
                    audio: None,
                    source: Some(trigger_source.unwrap_or_else(|| "proactive".to_string())),
                }
            ).await;
        }
    });

    // 5b. Ingestion Raw Audio -> Dispatcher
    let mut raw_audio_subscriber = nats_client.subscribe("io.user.speak.raw").await?;
    let dispatcher_audio = dispatcher.clone();
    let _audio_handle = tokio::spawn(async move {
        #[derive(Deserialize)]
        struct RawAudioPayload {
            audio: String,
            #[serde(default)]
            _format: String,
            #[serde(default)]
            speaker: Option<String>,
        }

        info!("👂 Cortex Ingress Listening on 'io.user.speak.raw'...");
        while let Some(msg) = raw_audio_subscriber.next().await {
            if let Ok(payload) = serde_json::from_slice::<RawAudioPayload>(&msg.payload) {
                let corr_id = Uuid::new_v4();
                let session_id = "global_session".to_string();
                let text = voice_prompt_text(&payload.speaker);

                dispatcher_audio.process_envelope(
                    corr_id,
                    &session_id,
                    EventPayload::PromptInbound {
                        text,
                        images: vec![],
                        audio: Some(payload.audio),
                        source: None,
                    }
                ).await;
            } else {
                warn!("⚠️ Ingression Error: Unsupported raw audio format.");
            }
        }
    });

    // 6. Ingestion Lobe -> Dispatcher
    let mut lobe_subscriber = nats_client.subscribe("lobe.fragment_stream").await?;
    let dispatcher_lobe = dispatcher.clone();
    let _lobe_handle = tokio::spawn(async move {
        info!("👂 Cortex Egress Listening on 'lobe.fragment_stream'...");
        while let Some(msg) = lobe_subscriber.next().await {
            match serde_json::from_slice::<FragmentPayload>(&msg.payload) {
                Ok(fragment) => {
                    let corr_id = Uuid::new_v4();
                    let session_id = "global_session".to_string(); 
                    
                    dispatcher_lobe.process_envelope(
                        corr_id,
                        &session_id,
                        EventPayload::LobeFragment {
                            sequence: fragment.sequence,
                            text: fragment.text.to_string(),
                            is_last: fragment.is_last,
                        }
                    ).await;
                },
                Err(e) => {
                    let err_msg = format!("JSON Parsing Failed: {}", e);
                    let corr_id = Uuid::new_v4();
                    dispatcher_lobe.process_envelope(
                        corr_id,
                        "system",
                        EventPayload::FatalSystemError(err_msg)
                    ).await;
                }
            }
        }
    });

    // 7. Graceful Shutdown
    tokio::signal::ctrl_c().await?;
    info!("🛑 SIGINT/CTRL+C received. Commencing graceful shutdown of Cortex...");
    
    info!("Draining pipelines...");
    let _ = nats_client.flush().await; // Attendre que NATS écoule les queues
    
    dispatcher_handle.abort(); // Tuer l'acteur
    info!("📴 Cortex Hub safely powered down.");
    
    Ok(())
}
