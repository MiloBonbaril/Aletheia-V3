import asyncio
import json
import nats
import sys
import os

async def main():
    print("⌨️ Démarrage du service I/O Text...")
    try:
        nc = await nats.connect("nats://localhost:4222")
        print("✅ Connecté au système nerveux (NATS).")
    except Exception as e:
        print(f"❌ Erreur de connexion à NATS: {e}")
        return

    print("=====================================================")
    print("Mode d'écriture multi-lignes.")
    print("Tapez votre message.")
    print("Commandes (seules sur une ligne) : ")
    print("  :w ou :send  -> Envoyer le message")
    print("  :q ou :quit  -> Quitter le service")
    print("  :c ou :clear -> Effacer le message en cours d'écriture")
    print("=====================================================\n")

    async def fragment_handler(msg):
        try:
            data = json.loads(msg.data.decode())
            text = data.get("text", "")
            is_last = data.get("is_last", False)
            
            if text:
                # Affichage du texte en cyan (\033[36m) pour le différencier
                print(f"\033[36m{text}\033[0m", flush=True)
                
            if is_last:
                print("\n> ", end="", flush=True)
        except Exception as e:
            print(f"\n[io_text] ❌ Erreur lors de la lecture du fragment: {e}")
            print("> ", end="", flush=True)

    await nc.subscribe("lobe.fragment_stream", cb=fragment_handler)

    current_message = []
    loop = asyncio.get_running_loop()

    def get_input():
        return sys.stdin.readline()

    print("> ", end="", flush=True)

    while True:
        try:
            # sys.stdin.readline() is blocking, but we run it in an executor thread
            # to not block the asyncio event loop (even though NATS heartbeat is handled automatically by nats-py)
            line = await loop.run_in_executor(None, get_input)
            if not line: # EOF (ex: Ctrl+D)
                break
            
            line_str = line.strip()
            
            if line_str in [":w", ":send"]:
                if current_message:
                    text_to_send = "".join(current_message).strip()
                    payload = {"text": text_to_send}
                    
                    try:
                        await nc.publish("io.user.msg.text", json.dumps(payload).encode())
                        print(f"\n[io_text] 📤 Message envoyé au cortex ({len(text_to_send)} caractères) !")
                    except Exception as e:
                        print(f"\n[io_text] ❌ Erreur d'envoi: {e}")
                    
                    current_message = [] # Reset après envoi
                    print("-----------------------------------------------------")
                    print("\n> ", end="", flush=True)
                else:
                    print("\n[io_text] ⚠️ Message vide, rien n'a été envoyé.")
                    print("> ", end="", flush=True)
            
            elif line_str in [":q", ":quit"]:
                print("\n⌨️ Arrêt du service I/O Text...")
                break
                
            elif line_str in [":c", ":clear"]:
                current_message = []
                print("\n[io_text] 🧹 Message effacé. Vous pouvez recommencer.")
                print("> ", end="", flush=True)
                
            else:
                # Ajout de la ligne au buffer de message courant
                current_message.append(line)
                
        except KeyboardInterrupt:
            print("\n⌨️ Interruption clavier, arrêt du service I/O Text...")
            break

    await nc.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
