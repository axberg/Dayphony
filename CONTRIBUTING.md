# Contributing to Dayphony

Thanks for helping improve Dayphony. Small, focused changes are easiest to review.

## Development setup

```bash
git clone https://github.com/axberg/Dayphony.git
cd Dayphony
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
```

Audio integration tests require Sonic Pi on macOS. Logic and OSC encoding tests do
not start an audio device and should remain portable.

## Pull requests

1. Open an issue before making a large architectural change.
2. Keep personal context and calendar data local by default.
3. Add tests for behavior that can be verified without audio hardware.
4. Run the complete test suite and a short dry run.
5. Explain privacy or permission changes explicitly in the pull request.

Do not commit generated audio, Sonic Pi binaries or SynthDefs, secrets, calendar
content, application histories, or other personal context.

## Musical changes

Music should change continuously rather than restart. Preserve the single transport,
smooth parameter changes, and schedule structural transitions on phrase boundaries.
New scenes should retain a pulse unless silence is an intentional safety or meeting
behavior.
