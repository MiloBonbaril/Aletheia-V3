# 🧠 Prompt engineering (Nexus-V)

The lobe_frontal builds a **structured XML system prompt**. The XML lets the LLM see the difference
between the sources of information. Thus the model applies the behavior rules with no confusion.

The `PromptBuilder` class (`services/lobe_frontal/src/prompt_builder.py`) builds the prompt.

## 🏗️ Structure of the system prompt

The prompt is in one `<system>` element. Each section has a different function:

```xml
<system>
  <persona>
    <!-- Identity, personality, tone, language rules and prohibitions -->
  </persona>

  <core_memory>
    <!-- Permanent facts, knowledge about the world, global state -->
  </core_memory>

  <users>
    <!-- Profiles of the known users, preferences, relation history -->
  </users>

  <tools>
    <!-- Short description of each available function -->
    <tool name="tool_name">
      What the tool does, and when to use it.
    </tool>
  </tools>

  <mood>
    <!-- The current mood of Aletheia. The lobe_frontal injects the last state that
         limbic published on limbic.mood.update. The section is absent while no mood
         is known, for example when limbic is not started. -->
  </mood>

  <recall>
    <!-- Related memories that the passive RAG found. The hippocampe supplies them
         for each user message. -->
  </recall>

  <context window="10min">
    <!-- Recent context summary from the hippocampe (optional) -->
  </context>
</system>
```

The `<persona>`, `<core_memory>`, `<users>` and `<tools>` sections are always present. The
`<mood>`, `<recall>` and `<context>` sections are present only when there is content for them.

## 🛠️ Tools (function calling)

The LLM uses function calling to control its memory, its mood and its silence.

| Tool | Function | When to use it |
|---|---|---|
| `get_from_memory` | Active RAG search | For a complex or specific search that the automatic `<recall>` did not cover. |
| `save_to_memory` | RAG storage | When Aletheia learns new and important information. |
| `stay_silent` | Flow control | To ignore a user, or to obey a request for silence. It generates no text. |
| `set_mood` | Mood | When a strong emotion occurs (joy, sadness, anger, a wish to tease). It takes `emotion`, `intensity` (0 to 1) and an optional free `description`. |

Notes:

- The passive RAG recalls the related memories automatically and puts them in the `<recall>`
  section. The `get_from_memory` tool is an addition for complex queries. It is not the primary
  path.
- `set_mood` only publishes the intention on `limbic.mood.set` (fire-and-forget). The `limbic`
  service is the only owner of the mood. It applies the change and publishes the canonical state on
  `limbic.mood.update`. The lobe_frontal keeps the last received value in memory and injects it in
  `<mood>` at the next turn. It never changes the mood locally.
- The lobe_frontal runs a maximum of 10 tool iterations for one turn
  (`MAX_TOOL_ITERATIONS`). It executes the tool calls of one iteration in parallel.
- The lobe_frontal writes the result of `get_from_memory` to the history
  (`hippocampe.history.add`, role `tool`).

## 📄 Configuration files

The content of the `<persona>`, `<core_memory>` and `<users>` sections comes from Markdown files in
`services/lobe_frontal/config/`:

| File | XML section |
|---|---|
| `PERSONA.md` | `<persona>` |
| `MEMORY.md` | `<core_memory>` |
| `USER.md` | `<users>` |

Edit these files to change the behavior of the entity. You do not have to change the code, and you
do not have to restart the service.

## 🖼️ Multimodal content

The `PromptBuilder.build()` method makes the message list for the inference:

- It adds the images as `image_url` parts of the user message.
- It adds the audio as an `input_audio` part, for a raw voice message.
- It removes each Discord image URL that is expired, or that expires in less than 300 s. An expired
  URL causes an error at the inference server.
- It repairs orphan `tool` roles that come from the history. A `tool` message with no related
  assistant tool call makes the inference fail.
