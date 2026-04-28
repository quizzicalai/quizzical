import React, { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useConfig } from '../context/ConfigContext';
import type { StaticContentBlock, StaticPageKey } from '../types/pages';
import { Spinner } from '../components/common/Spinner';

/** Renders a markdown string as rich HTML via react-markdown + GFM. */
const MarkdownContent: React.FC<{ content: string }> = ({ content }) => (
  <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
);

interface BlockRendererProps {
  block: StaticContentBlock;
}

const BlockRenderer: React.FC<BlockRendererProps> = ({ block }) => {
  switch (block.type) {
    case 'p':
      return <p className="mb-4">{block.text}</p>;
    case 'h2':
      return <h2 className="text-2xl font-semibold mt-6 mb-3">{block.text}</h2>;
    case 'ul':
      return (
        <ul className="list-disc list-inside mb-4 pl-4">
          {block.items.map((item, index) => <li key={index}>{item}</li>)}
        </ul>
      );
    case 'ol':
      return (
        <ol className="list-decimal list-inside mb-4 pl-4">
          {block.items.map((item, index) => <li key={index}>{item}</li>)}
        </ol>
      );
    case 'markdown':
      return <MarkdownContent content={block.text} />;
    default:
      return null;
  }
};

interface StaticPageProps {
  pageKey: StaticPageKey;
}

/**
 * Renders a static page's content (e.g., About, Terms, Donate) from the configuration.
 * Supports three content modes:
 *   1. `body` — a markdown string rendered as rich HTML (preferred for new content)
 *   2. `blocks` — an array of typed content blocks (legacy; kept for compatibility)
 *   3. Neither — renders a graceful "Content Not Available" fallback
 */
export const StaticPage: React.FC<StaticPageProps> = ({ pageKey }) => {
  const { config } = useConfig();
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, [pageKey]);

  if (!config) {
    return <Spinner message="Loading..." />;
  }

  const pageContent = config.content[pageKey];

  if (!pageContent) {
    return (
      <div className="flex-grow text-center py-10">
        <h1 className="text-2xl font-bold">Content Not Available</h1>
        <p className="text-muted">This page's content could not be loaded.</p>
      </div>
    );
  }

  return (
    <div className="flex-grow max-w-3xl mx-auto px-4 py-10">
      <article className="prose prose-slate dark:prose-invert max-w-none">
        <h1 ref={headingRef} tabIndex={-1} className="text-3xl font-bold mb-6 outline-none">
          {pageContent.title}
        </h1>
        {pageContent.body ? (
          <MarkdownContent content={pageContent.body} />
        ) : (
          (pageContent.blocks ?? []).map((block, index) => (
            <BlockRenderer key={index} block={block as StaticContentBlock} />
          ))
        )}
      </article>
    </div>
  );
};
