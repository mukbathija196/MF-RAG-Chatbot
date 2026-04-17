import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mutual Fund AI Assistant",
  description: "RAG-based FAQ chatbot for Nippon India Mutual Funds",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-gray-100 antialiased">{children}</body>
    </html>
  );
}
