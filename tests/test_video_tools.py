from __future__ import annotations

import unittest

from openlearn.video_tools import VideoDescriptor, VideoURLValidationError, describe_youtube_video


VIDEO_ID = "dQw4w9WgXcQ"


class VideoToolsTests(unittest.TestCase):
    def test_supported_urls_produce_same_privacy_enhanced_descriptor(self) -> None:
        urls = (
            f"https://youtube.com/watch?v={VIDEO_ID}",
            f"https://www.youtube.com/watch?v={VIDEO_ID}&t=43&si=tracking-value",
            f"https://m.youtube.com/watch?v={VIDEO_ID}",
            f"https://youtube.com/shorts/{VIDEO_ID}",
            f"https://www.youtube.com/shorts/{VIDEO_ID}/?feature=share",
            f"https://youtube.com/embed/{VIDEO_ID}",
            f"https://www.youtube.com/embed/{VIDEO_ID}?start=43",
            f"https://youtu.be/{VIDEO_ID}",
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(
                    describe_youtube_video(url),
                    VideoDescriptor(
                        provider="youtube",
                        video_id=VIDEO_ID,
                        canonical_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
                        embed_url=f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
                        requires_consent=True,
                    ),
                )

    def test_rejects_unsupported_or_unsafe_urls(self) -> None:
        urls = (
            "",
            "not a URL",
            f"http://youtube.com/watch?v={VIDEO_ID}",
            f"https://user:password@youtube.com/watch?v={VIDEO_ID}",
            f"https://youtube.com:443/watch?v={VIDEO_ID}",
            f"https://music.youtube.com/watch?v={VIDEO_ID}",
            f"https://www.youtu.be/{VIDEO_ID}",
            f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
            f"https://evil.example/youtube.com/watch?v={VIDEO_ID}",
            f"https://youtube.com/playlist?list={VIDEO_ID}",
            "https://youtube.com/watch?list=PL123",
            f"https://youtube.com/redirect?q=https%3A%2F%2Fyoutu.be%2F{VIDEO_ID}",
            f"https://youtube.com/watch?v={VIDEO_ID}&redirect=https%3A%2F%2Fevil.example",
            f"https://youtu.be/{VIDEO_ID}?next=https%3A%2F%2Fevil.example",
            "https://youtube.com/watch?v=too-short",
            f"https://youtube.com/watch?v={VIDEO_ID}&v=aaaaaaaaaaa",
            f"https://youtube.com/watch/{VIDEO_ID}",
            f"https://youtube.com/shorts/{VIDEO_ID}/extra",
            f"https://youtube.com/shorts//{VIDEO_ID}",
            f"https://youtu.be/path/{VIDEO_ID}",
            f"https://youtu.be//{VIDEO_ID}",
            f" https://youtu.be/{VIDEO_ID}",
            f"https://youtu.be/{VIDEO_ID}\n",
            f"https://youtu.be/{VIDEO_ID}%0a",
            f"https://youtube.com/watch?v={VIDEO_ID}&label=%0dsecret",
            f"https://youtube.com/watch?v={VIDEO_ID}#%0asecret",
            f"https://youtube.com/watch?v={VIDEO_ID}&bad=%zz",
        )

        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(VideoURLValidationError) as caught:
                    describe_youtube_video(url)
                self.assertEqual(str(caught.exception), "Enter a valid supported YouTube video URL.")

    def test_error_does_not_echo_the_input(self) -> None:
        secret = "api-key-super-secret"

        with self.assertRaises(VideoURLValidationError) as caught:
            describe_youtube_video(f"https://{secret}@evil.example/watch?v={VIDEO_ID}")

        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception))

    def test_rejects_non_string_input_without_leaking_it(self) -> None:
        with self.assertRaises(VideoURLValidationError) as caught:
            describe_youtube_video({"token": "super-secret"})  # type: ignore[arg-type]

        self.assertEqual(str(caught.exception), "Enter a valid supported YouTube video URL.")
        self.assertNotIn("super-secret", repr(caught.exception))


if __name__ == "__main__":
    unittest.main()
