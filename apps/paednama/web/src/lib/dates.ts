/** Whole days from now to a YYYY-MM-DD date (null when there is no date). */
export const daysTo = (d: string | null | undefined) => (d ? Math.ceil((new Date(d).getTime() - Date.now()) / 864e5) : null);
