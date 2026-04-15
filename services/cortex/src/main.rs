use async_nats::Client;
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use std::borrow::Cow;

#[derive(Serialize)]
struct PromptPayload {
    prompt: String,
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

    let client_clone = client.clone(); // Le client nats est safe à cloner (cheap)

    // Tâche 1 : Traitement du flux utilisateur, 100% isolée
    let user_task = tokio::spawn(async move {
        while let Some(msg) = user_msg_subscriber.next().await {
            // Supporte un JSON {"text": "..."} ou un texte brut
            let prompt_text = if let Ok(payload) = serde_json::from_slice::<UserMsgPayload>(&msg.payload) {
                payload.text
            } else if let Ok(text_slice) = std::str::from_utf8(&msg.payload) {
                text_slice.to_string()
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
                if let Err(e) = client_clone.publish("cortex.prompt", payload.into()).await {
                    println!("[Cortex] ⚠️ Erreur lors de l'envoi au Lobe Frontal: {}", e);
                }
            }
        }
    });

    // Tâche 2 : Traitement du stream de fragments
    let fragment_task = tokio::spawn(async move {
        while let Some(msg) = lobe_subscriber.next().await {
            match serde_json::from_slice::<FragmentPayload>(&msg.payload) {
                Ok(fragment) => {
                    println!("[Cortex] 🧩 Fragment reçu n°{} : {}", fragment.sequence, fragment.text);
                    
                    if fragment.is_last {
                        println!("[Cortex] ✅ Fin de transmission reçue du Lobe Frontal.\n");
                    }
                },
                Err(e) => {
                    eprintln!("[Cortex] 💀 FATAL: Erreur de parsing JSON d'un fragment.");
                    eprintln!(">>> Cause : {}", e);
                    eprintln!(">>> Payload corrompu : {:?}", std::str::from_utf8(&msg.payload).unwrap_or("BINARY/NON-UTF8"));
                }
            }
        }
    });

    // Maintien du processus en vie sur les deux threads asynchrones
    let _ = tokio::try_join!(user_task, fragment_task);

    Ok(())
}
