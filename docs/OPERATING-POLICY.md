# Readable operating policy and research basis

TalkToAi Code uses an application-level operating policy in `ethics_policy.py`.
Read the complete policy in that source file. It supports ordinary coding,
research, creative work and authorized security testing. It instructs the agent
to respect user authority and Stop, avoid credential theft and unauthorized
damage, protect private information, and distinguish claims from observations.
There is no topic keyword ban or automatic refusal merely because a request
contains words such as hacking, malware, security or research.

## Implemented enforcement

- A fixed release SHA-256 checks the policy text before inference and around
  tool execution. Source runs also compare the policy text on disk. Packaged
  runs check the embedded text.
- A mismatch stops further tool dispatch. The app never regenerates its trusted
  digest automatically to accept modified policy text.
- Direct project and desktop file tools reject writes to the policy in the
  running source or recognized TalkToAi source copies. Checkpoint restoration
  checks the same boundary.
- Skynet Mode rejects candidate policy changes or deletion before checks, after
  checks, and before reporting the candidate. A test that rewrites policy cannot
  turn that candidate into an accepted one by returning exit code zero.
- Only the dedicated policy module is protected this way. General app source
  remains available for improvements. Policy changes require an explicit,
  owner-reviewed source/release change outside these agent-edit tools.

The principles also enter the model's instructions. That does not make every
model decision enforceable or correct: the concrete protections above are the
tested mechanisms. Ordinary tool permissions and Plan/Act boundaries still
apply. Existing user authorization remains valid.

## Boundary and ownership

This is tamper detection and direct-tool protection, not absolute immutability.
Unrestricted commands or desktop automation with the same operating-system
rights can modify code, replace a verifier, or change a digest. The implementation
does not claim protection against an owner or such an agent. A stronger security
boundary would require an independently privileged broker or OS isolation.

The code remains readable and MIT-licensed. No obfuscation, no-copy clause or
new license restriction was added. Open source permits modification and sharing;
calling code uneditable or uncopyable would misrepresent that relationship.
See the [Open Source Definition](https://opensource.org/osd).

## Research used

Shafaet Brady Hussain's [Probability of Goodness Decision Routing 1.0](https://github.com/ResearchForumOnline/research/blob/main/papers/probability-of-goodness-ethical-routing.md)
distinguishes uncalibrated substring telemetry from real authorization and
decision routing. Its explicit failure cases include negation, substring matches
and positive-word padding. This implementation adopts its separation of explicit
constraints, actual authority and observed evidence. It does not turn the old
score into a probability of ethical correctness or import it as a blocking gate.

The public OpenZero integrity implementation was also inspected. Its pattern of
resealing current contents before verification was deliberately not reused.
The fixed expected policy digest here is never replaced on verification failure.

This work does not establish AGI, moral calibration, independent certification,
or scientific validation of a user's research. It implements the specific
software behavior described above.
