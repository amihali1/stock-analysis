import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Stock Analysis Platform",
  description: "ML-powered stock analysis with sentiment analysis",
};

function Nav() {
  return (
    <nav className="bg-gray-900 border-b border-gray-800">
      <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-6">
        <Link href="/" className="text-white font-bold text-lg">
          StockAnalysis
        </Link>
        <Link
          href="/"
          className="text-gray-400 hover:text-white text-sm transition-colors"
        >
          Dashboard
        </Link>
        <Link
          href="/?strategy=short"
          className="text-gray-400 hover:text-white text-sm transition-colors"
        >
          Shorts
        </Link>
        <Link
          href="/?strategy=options"
          className="text-gray-400 hover:text-white text-sm transition-colors"
        >
          Options
        </Link>
        <Link
          href="/paper-trades"
          className="text-gray-400 hover:text-white text-sm transition-colors"
        >
          Paper Trades
        </Link>
        <Link
          href="/watchlist"
          className="text-gray-400 hover:text-white text-sm transition-colors"
        >
          Watchlist
        </Link>
        <Link
          href="/backtest"
          className="text-gray-400 hover:text-white text-sm transition-colors"
        >
          Backtest
        </Link>
      </div>
    </nav>
  );
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-gray-950 text-gray-100 min-h-screen">
        <Nav />
        <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
