# SAKURA reference mix and missing-syllable repair

On 2026-10-19 the user requested a continuation of DSH's SAKURA mixes, with mixing guided by the original recording and a missing final syllable repaired near the third minute. The existing initial and DSH versions were retained.

The first arigatou phrase had an extended a vowel while its t-o syllable had been aligned ahead of the next phrase. Independent short reference crops identified the intended transition. The correction split the sustained note and reassigned the next phrase onset; total note count remained unchanged. Unrelated notes and all five control curves remained exact.

The revised score was imported into a new project with one existing Piapro instance. Native VSQX and PPSF readback verified notes, locked phonemes and control-value transitions. Actual audio was rendered and the affected vowel spectrum compared with other t-o vowels in the same synthesized song. This is acoustic support, not a subjective listening result. The project was saved natively; the temporary baseline was then restored and verified, followed by one normal application quit with exit code zero. The saved CPR was not reopened.

Mixing used the original vocal stem as a reference for bounded broad EQ, smooth phrase-level gain and stereo ambience. Only the affected native vocal phrases were replaced; effect returns were regenerated from the corrected lead. The original reference vocal was not mixed into the replacement lead. Existing instrumental transitions remain, with possible separation residue.

Full WAV, FLAC and decoded MP3 have identical duration and channel layout. FLAC matches WAV exactly; aligned stems reconstruct the master within quantization error. Sample, oversampled and input true-peak measurements passed. Loudness parsing used the complete final JSON object from FFmpeg, avoiding accidental reads of an initial silent measurement. The first render completed its audio exports before a metadata-only error; the metadata writer was corrected and all four existing audio files were independently recomputed and matched within quantization error without being rewritten.

The delivery contains the full mix, the repaired phrase excerpt, an RMS-matched comparison with DSH A, aligned stems and editable scores/settings. Technical and fitted reference metrics are not presented as a perceptual similarity score. No independent human listening review was performed. Music, lyrics, voice libraries, native projects, local paths, scripts, diagnostics and credentials are excluded from this repository.
