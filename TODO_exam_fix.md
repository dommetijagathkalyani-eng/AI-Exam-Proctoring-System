# Exam Selection & Evaluation Fix TODO

## Issue
- JS onclick="selectAnswer(${currentQuestion}, ${idx})" → literal string in HTML (broken handlers).
- JS questions shuffled + different from backend hardcoded → index mismatch, wrong grading.
- Nav not updating 'answered'.
- Backend results show different questions.

## Steps (0/4)
- [ ] 1. templates/student/exam.html: Event delegation, fixed questions matching backend, no shuffle, fix selectAnswer.
- [ ] 2. app.py: Update hardcoded questions to match JS exactly.
- [ ] 3. Test: Take exam → select options → submit → admin results correct.
- [ ] 4. Update TODO_exam_evaluation.md: Fixed.

**Progress**: 2/4 (selectAnswer event handler fixed)



