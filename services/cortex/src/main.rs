use async_nats::Client;
use futures::StreamExt;
use serde::{Deserialize, Serialize};

#[derive(Serialize)]
struct PromptPayload {
    prompt: String,
}

#[derive(Deserialize, Debug)]
struct FragmentPayload {
    sequence: usize,
    text: String,
    is_last: bool,
}

#[derive(Deserialize, Debug)]
struct UserMsgPayload {
    text: String,
}

#[tokio::main]
async fn main() -> Result<(), async_nats::Error> {
    println!("🧠 Cortex en cours de démarrage...");

    // 1. Connexion à NATS
    let client: Client = async_nats::connect("nats://localhost:4222").await?;
    println!("✅ Cortex connecté au système nerveux (NATS).");

    // 2. Souscription au stream de réponses du Lobe Frontal
    let mut lobe_subscriber = client.subscribe("lobe.fragment_stream").await?;
    println!("👂 Cortex en écoute sur 'lobe.fragment_stream'...");

    // 3. Souscription aux messages texte de l'utilisateur
    let mut user_msg_subscriber = client.subscribe("io.user.msg.text").await?;
    println!("👂 Cortex en écoute sur 'io.user.msg.text'...");

    println!("\n[Cortex] Prêt et en attente d'événements...");

    // 4. Boucle principale concurrente
    loop {
        tokio::select! {
            Some(msg) = user_msg_subscriber.next() => {
                // Supporte un JSON {"text": "..."} ou un texte brut
                let prompt_text = if let Ok(payload) = serde_json::from_slice::<UserMsgPayload>(&msg.payload) {
                    payload.text
                } else if let Ok(text) = String::from_utf8(msg.payload.to_vec()) {
                    text
                } else {
                    println!("[Cortex] ⚠️ Erreur: format de message utilisateur non supporté.");
                    continue;
                };

                println!("\n[Cortex] 📥 Message utilisateur reçu: {}", prompt_text);
                
                let prompt_msg = PromptPayload {
                    prompt: prompt_text,
                };
            
                if let Ok(payload) = serde_json::to_vec(&prompt_msg) {
                    println!("[Cortex] 📤 Envoi du prompt au Lobe Frontal...");
                    if let Err(e) = client.publish("cortex.prompt", payload.into()).await {
                        println!("[Cortex] ⚠️ Erreur lors de l'envoi au Lobe Frontal: {}", e);
                    }
                }
            }
            Some(msg) = lobe_subscriber.next() => {
                if let Ok(fragment) = serde_json::from_slice::<FragmentPayload>(&msg.payload) {
                    println!("[Cortex] 🧩 Fragment reçu n°{} : {}", fragment.sequence, fragment.text);
                    
                    if fragment.is_last {
                        println!("[Cortex] ✅ Fin de transmission reçue du Lobe Frontal.\n");
                    }
                } else {
                    println!("[Cortex] ⚠️ Erreur de parsing d'un fragment recu.");
                }
            }
        }
    }
}
