import React from 'react';
import { useConfig } from '../context/ConfigContext';

/**
 * A generic, reusable component for rendering simple static content pages.
 * It fetches its content from the global configuration object based on a key.
 *
 * @param {object} props - The component props.
 * @param {string} props.pageKey - The key in the config's content object
 * that corresponds to this page (e.g., 'aboutPage', 'privacyPolicyPage').
 */
function StaticPage({ pageKey }) {
  const config = useConfig();

  // Safely access the content for the specified page from the global config.
  // Provide a clear fallback object to prevent errors if the content is missing.
  const pageContent = config?.content?.[pageKey] || {
    title: 'Content Unavailable',
    paragraphs: ['The content for this page could not be loaded at this time. Please try again later.'],
  };

  return (
    <div className="max-w-3xl mx-auto px-4 py-8 sm:py-12 animate-fade-in">
      <h1 className="text-4xl font-extrabold text-primary mb-6 border-b pb-4">
        {pageContent.title}
      </h1>
      <div className="prose lg:prose-lg text-secondary space-y-4">
        {/* Render each string in the paragraphs array as a separate <p> tag */}
        {pageContent.paragraphs.map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </div>
    </div>
  );
}

/*
  --- HOW TO USE THIS COMPONENT IN YOUR ROUTER (App.js) ---

  1.  Add the content to your config (e.g., in your BFF's logic or mocks):
      content: {
        ...
        aboutPage: {
          title: 'About Quizzical.ai',
          paragraphs: [
            'Quizzical.ai is a project designed to explore...',
            'Our mission is to create delightful, AI-powered experiences.'
          ]
        },
        privacyPolicyPage: {
          title: 'Privacy Policy',
          paragraphs: ['Your privacy is important to us...']
        }
      }

  2.  Add the routes in your App.js file:
      <Route path="/about" element={<StaticPage pageKey="aboutPage" />} />
      <Route path="/privacy" element={<StaticPage pageKey="privacyPolicyPage" />} />
*/

export default StaticPage;
