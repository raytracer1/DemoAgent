import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'DemoAgent',
  description: 'AI-powered product demo video generator',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
