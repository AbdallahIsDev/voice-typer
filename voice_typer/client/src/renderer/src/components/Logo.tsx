interface LogoProps {
  size?: number
  className?: string
}

export function Logo({ size = 20, className }: LogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 148 148"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* Transparent background — uses currentColor for light/dark compatibility */}
      <rect x="18.5" y="55.5" width="18.5" height="37" rx="9.25" className="fill-current text-(--text-primary)" />
      <rect x="49.3333" y="37" width="18.5" height="74" rx="9.25" className="fill-current text-(--text-primary)" />
      <rect x="80.1667" y="18.5" width="18.5" height="111" rx="9.25" className="fill-current text-(--text-primary)" />
      <rect x="111" y="45.0938" width="18.5" height="57.8125" rx="9.25" className="fill-current text-(--text-primary)" />
    </svg>
  )
}