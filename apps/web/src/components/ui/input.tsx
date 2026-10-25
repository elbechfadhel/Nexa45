import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"
import { cn } from "cn"

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        // charte Transliate : classe .input du pack (rayon 8px, focus bleu brand)
        "input min-w-0 file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground disabled:pointer-events-none aria-invalid:border-destructive aria-invalid:focus:ring-2 aria-invalid:focus:ring-destructive",
        className
      )}
      {...props}
    />
  )
}

export { Input }
