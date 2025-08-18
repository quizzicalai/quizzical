// frontend/src/components/common/Turnstile.tsx
import React, { useEffect, useRef } from 'react';

declare global {
    interface Window {
        turnstile: any;
    }
}

const Turnstile = () => {
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (ref.current) {
        window.turnstile.render(ref.current, {
            sitekey: import.meta.env.VITE_TURNSTILE_SITE_KEY, 
            });
        }
    }, []);

    return <div ref={ref} />;
};

export default Turnstile;