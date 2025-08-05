import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

/**
 * A custom hook that calls a function whenever the route changes.
 * This is useful for closing mobile menus or other overlays during navigation.
 * @param close - The function to call on route change.
 */
export function useCloseOnRouteChange(close: () => void) {
  const { pathname, search, hash } = useLocation();

  useEffect(() => {
    close();
    // The dependency array ensures this effect runs on every part of the URL changing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, search, hash]);
}
