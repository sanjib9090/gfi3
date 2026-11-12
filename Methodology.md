Methodology

1. Tally Description

The flask app will send a request to Tally to get each of the job descriptions. It will extract the text and save it as JSON. The roles, criteria, keywords and weights are all in a file called data/roles.json.

If there is an issue with the request to Tally then the saved description will be used. If there hasn't been any saved then a snapshot will be used. A warning will appear. The description will then be mapped to the criteria provided in data/roles.json. If there are too few criteria available it will not score the resume.

2. Resume Extraction

There are 47 resumes provided. Each one has a unique filename and links to the preview in WorkDrive. One has no text extracted. This will be marked as needing review.

If there is a new upload the file will be extracted into text. All extra white space will be cleaned up. Common variations of section headers will be identified and the resume split into sections. If a section header is not found then a conservative guess will be made based on keywords. This section will be marked as a guess. If text cannot be extracted due to shortness or unreadability then it will be marked. If it is an unsupported or broken file then there will be an error message.

The original upload will be saved to Supabase along with the extracted resume. This way the reviewer can open the original resume. The parser will not do OCR so odd formatting may cause issues with the extracted text not being complete or in the right order. The extracted resume should be compared to the original. Not all resumes will be completely correct.

3. Scoring based on Evidence

Each resume will be scored based on the criteria provided against each role. The keywords can be customised. The resume will show what text has been found and what text is from the job description.

Score = (points achieved / max points for criteria used) 100. Rounded to the nearest whole number. Criteria will only be used if they can be mapped to the job description. Most criteria will give the max points if there is a match using the keywords. Some criteria will give part marks based on the UI/UX criteria. This will show how much evidence has been found in the resume. It won't show how likely they are to succeed or that they have the skill.

LCA Role

Criteria Max Points

Life Cycle Assessment / Analysis 25

GaBi or SimaPro 20

Inventory / Modelling / Data Quality 15

ISO 14040 / 14044 10

Evidence of LCA project 10

LCA Experience 10

Environmental Product Declaration 5

Working with stakeholders / cross-functional teams 5

Total 100

For LCA Experience the highest value between the LCA Experience and guessed based on the time in relevant roles will be used. It will not be added together. The criteria will give 3 points for less than 2 years, 6 points for 2-4 years and 10 points for 4+ years. If there are overlapping roles with dates then only one will be counted. Present will be calculated based on the analysis date. If the dates aren't complete then they will be guessed. The time guessed won't show that they have done LCA tasks for that long or ran projects independently.

The assignment states 4-6 years but the LCA description states 2-3 years of independently running LCA projects. This will be highlighted in the interface for manual checking. [[1]](https://tally.so/r/9q5plp)

ISO :2006 will be highlighted. Just because there is a year doesn't mean it is ISO 14040 / 14044.

UI/UX Internship

Criteria Max Points

UI/UX / Human Centred Design 25

Figma 20

User Research / Testing 20

Prototyping / Wireframing 15

Evidence of relevant design project 10

Cross functional 5

Visual Design / Design Systems 5

Total 100

Some criteria will give partial points if there is a partial match. Evidence of working on responsive UI's doesn't guarantee that they have Figma or user research experience. The time spent on UI/UX experience will be shown but no points will be given.

4. Categorising

Uncertain - There was an error or warning during extraction.

Neither/Uncertain - Scores are less than 30 for both roles.

Both - Scores are 30+ and the difference between the two scores is less than 15.

LCA/UI/UX - Otherwise whichever score is higher will be the best fit.

These are just heuristic values from the prototype. They aren't actual cut off points for hiring. The candidates will be sorted in descending order based on their scores for each role. If there is a tie then the filename will be used. New uploads will be scored using the same rules and added to the pile of candidates.

5. Manual Checking

The interface allows filtering between roles, searching, filtering by category, sorting by score/name and viewing sections of the resume. It also shows evidence and breakdown of scores, highlights experience and shows errors. Links to the original resume.

This is a deterministic approach and can be explained. However, there could be issues with the keywords not giving enough context. It could inflate how good the candidate is. The lack of text doesn't guarantee they don't have the qualification. The reviewer should check the original resume and make sure they meet all the criteria from the job. Not all criteria are taken into consideration when scoring.