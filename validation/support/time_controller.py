"""
Time manipulation controller for V2 validation scenarios.

Provides date/time control for testing @run-on directives and time patterns.
"""

from datetime import datetime, timedelta

from core.logger import UnifiedLogger


class TimeController:
    """Controls system time for validation testing."""

    def __init__(self):
        self.logger = UnifiedLogger(tag="time-controller")
        self.current_test_date: datetime | None = None
        self._datetime_patches = []

    def set_date(self, date_str: str):
        """Set system date for testing."""
        # Parse date string (support various formats)
        try:
            # Try common formats
            for fmt in ["%Y-%m-%d", "%B %d, %Y", "%A, %B %d, %Y"]:
                try:
                    test_date = datetime.strptime(date_str.strip(), fmt)
                    break
                except ValueError:
                    continue
            else:
                # If no format matched, try with time
                test_date = datetime.fromisoformat(
                    date_str.replace("Monday, ", "").replace("Tuesday, ", "")
                )
        except ValueError as exc:
            raise ValueError(f"Unable to parse date: {date_str}") from exc

        self.current_test_date = test_date
        self.logger.info(f"Set test date to: {test_date}")

    def advance_time(self, hours: int = 1, minutes: int = 0):
        """Advance time by specified amount."""
        if not self.current_test_date:
            raise RuntimeError("Must set initial date before advancing time")

        self.current_test_date += timedelta(hours=hours, minutes=minutes)
        self.logger.info(f"Advanced time to: {self.current_test_date}")
