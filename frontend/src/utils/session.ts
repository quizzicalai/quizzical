// frontend/src/utils/session.ts

/**
 * Session management utilities for quiz state persistence.
 * Uses sessionStorage to maintain quiz context within a browser session.
 */

const STORAGE_KEYS = {
  QUIZ_ID: 'quizzical_quiz_id',
  QUIZ_STATE: 'quizzical_quiz_state',
  QUIZ_TIMESTAMP: 'quizzical_quiz_timestamp',
} as const;

const IS_DEV = import.meta.env.DEV === true;
const SESSION_TIMEOUT_MS = 3600000; // 1 hour

/**
 * Safely interacts with sessionStorage with fallback for SSR/errors
 */
class SessionManager {
  private isAvailable(): boolean {
    try {
      const test = '__session_test__';
      sessionStorage.setItem(test, test);
      sessionStorage.removeItem(test);
      return true;
    } catch {
      return false;
    }
  }

  private log(action: string, details?: any): void {
    if (IS_DEV) {
      console.log(`[SessionManager] ${action}`, details || '');
    }
  }

  /**
   * Gets the current quiz ID from session storage
   */
  getQuizId(): string | null {
    if (!this.isAvailable()) return null;
    
    try {
      const id = sessionStorage.getItem(STORAGE_KEYS.QUIZ_ID);
      
      // Validate session hasn't expired
      if (id && this.isSessionExpired()) {
        this.log('Session expired, clearing quiz ID');
        this.clearSession();
        return null;
      }
      
      return id;
    } catch (error) {
      this.log('Error reading quiz ID', error);
      return null;
    }
  }

  /**
   * Saves the quiz ID to session storage with timestamp
   */
  saveQuizId(quizId: string): void {
    if (!this.isAvailable()) {
      this.log('SessionStorage not available');
      return;
    }

    try {
      sessionStorage.setItem(STORAGE_KEYS.QUIZ_ID, quizId);
      sessionStorage.setItem(
        STORAGE_KEYS.QUIZ_TIMESTAMP, 
        Date.now().toString()
      );
      this.log('Saved quiz ID', { quizId });
    } catch (error) {
      this.log('Error saving quiz ID', error);
      // Don't throw - gracefully degrade
    }
  }

  /**
   * Clears the quiz ID from session storage
   */
  clearQuizId(): void {
    if (!this.isAvailable()) return;

    try {
      sessionStorage.removeItem(STORAGE_KEYS.QUIZ_ID);
      sessionStorage.removeItem(STORAGE_KEYS.QUIZ_TIMESTAMP);
      sessionStorage.removeItem(STORAGE_KEYS.QUIZ_STATE);
      this.log('Cleared quiz session');
    } catch (error) {
      this.log('Error clearing quiz ID', error);
    }
  }

  /**
   * Saves partial quiz state for recovery
   */
  saveQuizState(state: QuizStateSnapshot): void {
    if (!this.isAvailable()) return;

    try {
      const snapshot = {
        ...state,
        timestamp: Date.now(),
      };
      sessionStorage.setItem(
        STORAGE_KEYS.QUIZ_STATE, 
        JSON.stringify(snapshot)
      );
      this.log('Saved quiz state', snapshot);
    } catch (error) {
      this.log('Error saving quiz state', error);
    }
  }

  /**
   * Retrieves saved quiz state for recovery
   */
  getQuizState(): QuizStateSnapshot | null {
    if (!this.isAvailable()) return null;

    try {
      const stateJson = sessionStorage.getItem(STORAGE_KEYS.QUIZ_STATE);
      if (!stateJson) return null;

      const state = JSON.parse(stateJson);
      
      // Validate state isn't stale
      if (this.isSessionExpired(state.timestamp)) {
        this.clearSession();
        return null;
      }

      return state;
    } catch (error) {
      this.log('Error reading quiz state', error);
      return null;
    }
  }

  /**
   * Checks if the session has expired
   */
  private isSessionExpired(timestamp?: number): boolean {
    const savedTimestamp = timestamp || this.getTimestamp();
    if (!savedTimestamp) return false;
    
    const elapsed = Date.now() - savedTimestamp;
    return elapsed > SESSION_TIMEOUT_MS;
  }

  /**
   * Gets the session timestamp
   */
  private getTimestamp(): number | null {
    try {
      const timestamp = sessionStorage.getItem(STORAGE_KEYS.QUIZ_TIMESTAMP);
      return timestamp ? parseInt(timestamp, 10) : null;
    } catch {
      return null;
    }
  }

  /**
   * Clears all session data
   */
  clearSession(): void {
    this.clearQuizId();
  }

  /**
   * Migrates session data if schema changes (future-proofing)
   */
  migrateSession(): void {
    // Placeholder for future migrations
    // Example: converting old session format to new format
  }
}

// Type definitions
export interface QuizStateSnapshot {
  quizId: string;
  currentView: 'synopsis' | 'question' | 'result';
  answeredCount: number;
  knownQuestionsCount: number;
  timestamp?: number;
}

// Create singleton instance
const sessionManager = new SessionManager();

// Export individual functions for backward compatibility
export const getQuizId = () => sessionManager.getQuizId();
export const saveQuizId = (quizId: string) => sessionManager.saveQuizId(quizId);
export const clearQuizId = () => sessionManager.clearSession();

// Export additional functionality
export const saveQuizState = (state: QuizStateSnapshot) => sessionManager.saveQuizState(state);
export const getQuizState = () => sessionManager.getQuizState();
export const clearSession = () => sessionManager.clearSession();

// Export the manager for advanced use cases
export { sessionManager };