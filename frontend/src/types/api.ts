// src/types/api.ts

/**
 * The normalized shape for all API errors handled by the application.
 */
export type ApiError = {
  status?: number;
  code?: string;
  message?: string;
  retriable?: boolean;
  details?: any; // For development-only debugging
};