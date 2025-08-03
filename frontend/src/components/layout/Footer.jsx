import React from 'react';
import { useConfig } from '../../context/ConfigContext';

/**
 * A dedicated, accessible, and robust Footer component.
 */
function Footer() {
  const config = useConfig();
  
  // Safely access config values with sensible defaults
  const brandName = config?.content?.brand?.name || 'Quizzical.ai';
  const copyrightText = config?.content?.footer?.copyright || `© ${new Date().getFullYear()}`;
  const navLinks = config?.content?.footer?.navLinks || [];

  return (
    <footer className="w-full p-4 text-secondary" role="contentinfo">
      <div className="max-w-5xl mx-auto flex items-center justify-between text-sm">
        <p className="font-bold">{brandName}</p>
        
        {/* Render navigation only if links are available in the config */}
        {navLinks.length > 0 && (
          <nav aria-label="Secondary navigation">
            <ul className="hidden sm:flex items-center space-x-6">
              {navLinks.map((link) => (
                <li key={link.text}>
                  <a 
                    href={link.href} 
                    className="hover:text-primary transition-colors"
                    // Security best practice for external links
                    target="_blank" 
                    rel="noopener noreferrer"
                  >
                    {link.text}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </div>
    </footer>
  );
}

export default Footer;
