'use client';

import { useEffect, useState } from 'react';

export default function Home() {
  const [video, setVideo] = useState<string | null>(null);

  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const v = p.get('video');
    if (v) setVideo(decodeURIComponent(v));
  }, []);

  return (
    <main className="max-w-[720px] mx-auto px-5 py-10">
      <h1 className="text-[28px] font-bold text-white mb-1">🎬 DemoAgent</h1>
      <p className="text-[#888] text-[15px] mb-8">Your demo video is ready</p>

      {video ? (
        <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-6">
          <video className="w-full rounded-lg border border-[#2a2a2a]" src={video} controls autoPlay />
        </div>
      ) : (
        <div className="bg-[#1a1a1a] border border-[#2a2a2a] rounded-xl p-6 text-center text-[#555]">
          No video URL provided. Start a demo from the Chrome extension.
        </div>
      )}
    </main>
  );
}
