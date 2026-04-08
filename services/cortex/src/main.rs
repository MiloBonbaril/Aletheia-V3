use async_nats::Client;
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use std::time::Duration;
use tokio::time;

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

#[tokio::main]
async fn main() -> Result<(), async_nats::Error> {
    println!("🧠 Cortex en cours de démarrage...");

    // 1. Connexion à NATS
    let client: Client = async_nats::connect("nats://localhost:4222").await?;
    println!("✅ Cortex connecté au système nerveux (NATS).");

    // 2. Souscription au stream de réponses du Lobe Frontal
    let mut subscriber = client.subscribe("lobe.fragment_stream").await?;
    println!("👂 Cortex en écoute sur 'lobe.fragment_stream'...");

    // 3. Boucle principale simulée
    // On attend un peu que tout soit en place
    time::sleep(Duration::from_secs(2)).await;

    // Simulation d'un événement déclencheur (ex: VTubeStudio / Chat Twitch / Proactivité)
    let prompt_msg = PromptPayload {
        prompt: "Salut l'IA ! Que penses-tu de l'architecture événementielle ?".to_string(),
    };

    let payload = serde_json::to_vec(&prompt_msg).unwrap();
    println!("\n[Cortex] 📤 Envoi du prompt au Lobe Frontal...");
    client.publish("cortex.prompt", payload.into()).await?;

    // 4. Écoute active des fragments asynchrones provenant du LLM
    println!("[Cortex] ⏳ Attente de la réponse en streaming...\n");
    
    while let Some(msg) = subscriber.next().await {
        if let Ok(fragment) = serde_json::from_slice::<FragmentPayload>(&msg.payload) {
            println!("[Cortex] 🧩 Fragment reçu n°{} : {}", fragment.sequence, fragment.text);
            
            // Ici, à l'avenir, le Cortex pourrait relayer le fragment test au système TTS / VTube
            
            if fragment.is_last {
                println!("\n[Cortex] ✅ Fin de transmission reçue du Lobe Frontal.");
                break;
            }
        } else {
            println!("[Cortex] ⚠️ Erreur de parsing d'un fragment recu.");
        }
    }

    println!("Cortex termine son exécution avec succès.");
    Ok(())
}
