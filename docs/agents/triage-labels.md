# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ----------------------------------------- |
| `needs-triage`              | _no label_            | Maintainer needs to evaluate this issue  |
| `needs-info`                | `needs-info`          | Waiting on reporter for more information |
| `ready-for-agent`           | `ready-for-agent`     | Fully specified, ready for an AFK agent  |
| `ready-for-human`           | `ready-for-human`     | Requires human implementation            |
| `wontfix`                   | `wontfix`             | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

The `needs-triage` row has no label string. Do not use the words of that cell as a label name: the
label does not exist, thus `gh issue edit --add-label` fails on it. Read the section below for the
operation that puts an issue in the triage state.

Edit the right-hand column to match whatever vocabulary you actually use.

## The absence of a label is the triage state

There is no `needs-triage` label in this repo, and you must not create one. An open issue with no
triage label **is** an issue to triage. The absence carries the meaning.

Apply this rule:

- To put an issue in the triage state, remove its triage label. Do not add a label.
- To take an issue out of the triage state, add one of the four labels above.
- An issue carries one triage label at most.

The reason is maintenance. A `needs-triage` label says the same thing as the absence of a label,
thus the two can disagree. A person who triages an issue must then remember to remove one label and
to add another one. The rule above keeps one source of truth.

The `Issues` board of the `services/terminal` console shows one column for each triage state. The
first column holds the open issues with no triage label. It fills itself.
