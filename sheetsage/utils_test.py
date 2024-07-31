import hashlib
import pathlib
import subprocess
import tempfile
import unittest

import librosa
import numpy as np

from .assets import retrieve_asset
from .utils import (
    compute_checksum,
    decode_audio,
    encode_audio,
    engrave,
    get_approximate_audio_length,
    retrieve_audio_bytes,
    run_cmd_sync,
)


def _quick_spec(sr, audio):
    assert sr % 22050 == 0
    mel = librosa.feature.melspectrogram(
        y=audio[:, 0], sr=sr, hop_length=sr // 22050 * 512, fmax=11025
    )
    assert mel.max() < 300.0
    lmel = librosa.power_to_db(mel, ref=300.0)
    return lmel


class TestUtils(unittest.TestCase):
    def test_compute_checksum(self):
        with tempfile.NamedTemporaryFile() as f:
            path = pathlib.Path(f.name)
            with open(path, "w") as f:
                f.write("foo")
            self.assertEqual(
                compute_checksum(path),
                "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
            )
            self.assertEqual(
                compute_checksum("foo".encode("utf-8")),
                "2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae",
            )
            self.assertEqual(
                compute_checksum(path, algorithm="md5"),
                "acbd18db4cc2f85cedef654fccc4a4d8",
            )
            for algorithm in hashlib.algorithms_guaranteed:
                if algorithm.startswith("shake"):
                    continue
                checksum = compute_checksum(path, algorithm=algorithm)
                self.assertTrue(isinstance(checksum, str))
                self.assertTrue(checksum.strip(), checksum)
                self.assertGreater(len(checksum), 0)

        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            with self.assertRaises(FileNotFoundError):
                compute_checksum(pathlib.Path(d, "nonexistent"))
            with self.assertRaises(IsADirectoryError):
                compute_checksum(d)

        with self.assertRaises(ValueError):
            compute_checksum(None, algorithm="shake_128")
        with self.assertRaises(ValueError):
            compute_checksum(None, algorithm="foo256")

    def test_run_cmd_sync(self):
        status, stdout, stderr = run_cmd_sync(
            "ls", cwd=pathlib.Path(__file__).resolve().parent
        )
        self.assertEqual(status, 0)
        self.assertTrue(pathlib.Path(__file__).parts[-1] in stdout)
        self.assertEqual(stderr, "")
        with self.assertRaises(FileNotFoundError):
            run_cmd_sync("")
        with self.assertRaises(FileNotFoundError):
            run_cmd_sync("itwouldbereallyunusualforthistobethenameofaprogram")
        with self.assertRaises(NotADirectoryError):
            run_cmd_sync("ls", cwd=pathlib.Path(__file__).resolve())
        with self.assertRaises(subprocess.TimeoutExpired):
            run_cmd_sync("sleep 1", timeout=1e-3)

    def test_retrieve_audio_bytes(self):
        # NOTE: CC-BY as of 22-06-26.
        # Thanks to uploader 2-Minute Design: https://www.youtube.com/c/2-minutedesign
        youtube_url = "https://www.youtube.com/watch?v=PPu1ekDSKw8"
        audio_bytes, name = retrieve_audio_bytes(youtube_url, return_name=True)
        self.assertEqual(len(audio_bytes), 136212)
        self.assertEqual(
            compute_checksum(audio_bytes),
            "9c219e41c223e54642c14fe5eadf27bc792d8ce17baae993461d82639827093f",
        )
        self.assertEqual(
            name, "10-Second Timer _ Red Digital On White Background [PPu1ekDSKw8].webm"
        )
        sr, audio = decode_audio(audio_bytes)
        self.assertEqual(sr, 48000)
        self.assertEqual(audio.shape, (552821, 2))
        with self.assertRaises(subprocess.TimeoutExpired):
            retrieve_audio_bytes(youtube_url, timeout=1e-3)
        with self.assertRaisesRegex(Exception, "Unsupported URL"):
            retrieve_audio_bytes("https://www.google.com")
        with self.assertRaisesRegex(Exception, "Failed to retrieve"):
            retrieve_audio_bytes("https://www.youtube.com/watch?v=00000000000")
        with self.assertRaisesRegex(ValueError, "Specified url is too large"):
            retrieve_audio_bytes(youtube_url, max_filesize_mb=0)
        with self.assertRaisesRegex(ValueError, "Specified url is too long"):
            retrieve_audio_bytes(youtube_url, max_duration_seconds=5)

    def test_decode_audio(self):
        # Load uncompressed WAV
        raw_sr, raw_audio = decode_audio(retrieve_asset("TEST_WAV"))
        self.assertEqual(raw_sr, 44100)
        self.assertEqual(raw_audio.dtype, np.float32)
        self.assertEqual(raw_audio.shape, (202311, 2))
        self.assertAlmostEqual(np.abs(raw_audio).max(), 0.29, places=2)

        # Load compressed MP3
        mp3_path = retrieve_asset("TEST_MP3")
        enc_sr, enc_audio = decode_audio(mp3_path)
        self.assertEqual(enc_sr, 22050)
        self.assertEqual(enc_audio.dtype, np.float32)
        self.assertEqual(enc_audio.shape, (101155, 2))
        self.assertAlmostEqual(np.abs(enc_audio).max(), 0.29, places=2)

        # Ensure they're more or less the same audio
        self.assertAlmostEqual(
            np.abs(
                _quick_spec(raw_sr, raw_audio) - _quick_spec(enc_sr, enc_audio)
            ).mean(),
            2.2,
            places=1,
        )

        # Test bytes loading
        with open(mp3_path, "rb") as f:
            audio_bytes = f.read()
        sr, audio = decode_audio(audio_bytes)
        self.assertTrue(np.array_equal(audio, enc_audio))

        # Test resampling
        sr, audio = decode_audio(mp3_path, sr=44100)
        self.assertEqual(sr, 44100)
        self.assertEqual(audio.shape, (202310, 2))

        # Test offset / duration limiting
        sr, audio = decode_audio(mp3_path, offset=2, duration=1)
        self.assertEqual(sr, 22050)
        self.assertEqual(audio.shape, (22050, 2))
        self.assertAlmostEqual(np.abs(audio).max(), 0.21, places=2)

        # Test out of bounds offsets
        sr, audio = decode_audio(mp3_path, offset=-1)
        self.assertEqual(audio.shape[0], 101155)
        sr, audio = decode_audio(mp3_path, offset=10)
        self.assertEqual(audio.shape[0], 0)

        # Test zero duration
        sr, audio = decode_audio(mp3_path, duration=0)
        self.assertEqual(audio.shape[0], 0)

        # Test mono
        sr, audio = decode_audio(mp3_path, mono=True)
        self.assertEqual(audio.shape, (101155, 1))

        # Test normalize
        sr, audio = decode_audio(mp3_path, normalize=True)
        self.assertAlmostEqual(np.abs(audio).max(), 1.0)

        # Test error handling
        with tempfile.NamedTemporaryFile() as f:
            with self.assertRaises(FileNotFoundError):
                decode_audio(f.name + ".noexist")
            with open(f.name, "w") as f:
                f.write("Text data cannot be decoded as audio.")
            with self.assertRaisesRegex(RuntimeError, "Unknown audio format"):
                decode_audio(f.name)

    def test_encode_audio(self):
        sr, audio = decode_audio(retrieve_asset("TEST_MP3"))
        for codec, expected_err in zip([".ogg", ".wav"], [1.24, 0]):
            with tempfile.NamedTemporaryFile(suffix=codec) as f:
                encode_audio(f.name, sr, audio)
                sr_hat, audio_hat = decode_audio(f.name)
            self.assertEqual(sr_hat, sr)
            self.assertEqual(audio.shape, (101155, 2))
            self.assertEqual(audio_hat.shape, (101155, 2))
            self.assertAlmostEqual(
                np.abs(_quick_spec(sr, audio) - _quick_spec(sr, audio_hat)).mean(),
                expected_err,
                places=2,
            )
        with tempfile.NamedTemporaryFile(suffix=".ogg") as f:
            encode_audio(f.name, sr, np.zeros((0, 1), dtype=np.float32))
            sr, audio = decode_audio(f.name)
            self.assertEqual(audio.shape, (0, 1))
        with tempfile.NamedTemporaryFile(suffix=".ogg") as f:
            with self.assertRaises(subprocess.TimeoutExpired):
                encode_audio(f.name, sr, audio, timeout=1e-4)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(Exception, "No such file or directory"):
                encode_audio(d + "/doesnotexist/test.wav", sr, audio)
        with tempfile.NamedTemporaryFile(suffix=".unknowncodec") as f:
            with self.assertRaisesRegex(Exception, "suitable output format"):
                encode_audio(f.name, sr, audio)

    def test_get_approximate_audio_length(self):
        mp3_path = retrieve_asset("TEST_MP3")
        duration_approx = get_approximate_audio_length(mp3_path)
        self.assertAlmostEqual(duration_approx, 4.65, places=2)

    def test_engrave(self):
        # Test simple
        lilypond = """{c' e' g' e'}"""
        self.assertEqual(
            compute_checksum(engrave(lilypond)),
            "171de501a9f35f5aad2effed6652ab2f69de0461c3a73eef7dff402b34163bb8",
        )
        self.assertEqual(
            compute_checksum(
                engrave(lilypond, transparent=False, trim=False, hide_footer=False)
            ),
            "e6770af93dbc3f013936ff141d55f25b8a14032a22a49d79d28670de9f51051f",
        )
        self.assertNotEqual(
            compute_checksum(engrave(lilypond, out_format="pdf")),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

        # Test logo
        lilypond = r"""
{
    \once \override Staff.TimeSignature #'stencil = ##f
    \time 3/4
    \relative c'' {
        a g e ||
    }
}
        """.strip()
        self.assertEqual(
            compute_checksum(engrave(lilypond)),
            "65612051db7398924340d5b8b3c059d5d743e4f82b7685b4dcfa215c53089dcb",
        )
