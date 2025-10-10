// frontend/src/components/common/IconButton.tsx
import React, { forwardRef } from 'react';
import clsx from 'clsx';

type IconComponent = React.ComponentType<React.SVGProps<SVGSVGElement>>;

export interface IconButtonProps {
  Icon: IconComponent;
  label: string;
  type?: 'button' | 'submit' | 'reset';
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  disabled?: boolean;
  className?: string;
  iconClassName?: string;
  size?: 'sm' | 'md' | 'lg';
  variant?: 'primary' | 'neutral' | 'danger';
  /** Allow consumers to pass inline styles (e.g., fontSize) */
  style?: React.CSSProperties;
}

const SIZE: Record<NonNullable<IconButtonProps['size']>, { btn: string; iconPx: number }> = {
  sm: { btn: 'w-10 h-10', iconPx: 18 }, // 40px
  md: { btn: 'w-11 h-11', iconPx: 22 }, // 44px
  lg: { btn: 'w-12 h-12', iconPx: 26 }, // 48px
};

// Map variant -> CSS var token with numeric RGB fallback
const TOKEN: Record<
  NonNullable<IconButtonProps['variant']>,
  { var: string; fallback: string; textOnBg?: string }
> = {
  primary: { var: '--color-primary', fallback: '79 70 229', textOnBg: '255 255 255' }, // indigo-700, white text
  neutral: { var: '--color-neutral', fallback: '148 163 184' },
  danger:  { var: '--color-accent',  fallback: '234 179 8'  },
};

// Only CSS custom properties (keys starting with `--`) must be string/number.
// This avoids the Record<string,string> clash with numeric CSSProperties.
type CSSVarStyle = React.CSSProperties & Record<`--${string}`, string | number>;

const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  (
    {
      Icon,
      label,
      type = 'button',
      onClick,
      disabled = false,
      className,
      iconClassName,
      size = 'md',
      variant = 'primary',
      style: userStyle,
    },
    ref
  ) => {
    const s = SIZE[size];
    const t = TOKEN[variant];

    // Inline fallbacks guarantee visibility even before ThemeInjector runs.
    const bgEnabled = `rgb(var(${t.var}, ${t.fallback}))`;
    const bgDisabled = `rgb(var(--color-border, 226 232 240))`;
    const textEnabled = t.textOnBg ? `rgb(${t.textOnBg})` : `rgb(var(--color-fg, 15 23 42))`;
    const textDisabled = `rgba(var(--color-fg, 15 23 42), 0.55)`;
    const ringColor = `rgba(var(--color-ring, 129 140 248), 0.5)`; // tailwind ring var

    const computed: CSSVarStyle = {
      backgroundColor: disabled ? bgDisabled : bgEnabled,
      color: disabled ? textDisabled : textEnabled,
      ['--tw-ring-color']: ringColor,
    };

    // Merge consumer-provided styles last so they override defaults.
    const style: CSSVarStyle = {
      ...computed,
      ...(userStyle || {}),
    };

    return (
      <button
        ref={ref}
        type={type}
        onClick={onClick}
        aria-label={label}
        disabled={disabled}
        style={style}
        className={clsx(
          'p-0 border-0 shrink-0',
          'inline-flex items-center justify-center rounded-full align-middle',
          'transition-all transform focus:outline-none focus:ring-2',
          'hover:scale-105 disabled:cursor-not-allowed disabled:scale-100',
          !disabled && 'hover:brightness-95',
          s.btn,
          className
        )}
      >
        <Icon
          width={s.iconPx}
          height={s.iconPx}
          className={clsx('pointer-events-none', iconClassName)}
          aria-hidden
          focusable="false"
        />
      </button>
    );
  }
);

IconButton.displayName = 'IconButton';
export default IconButton;
