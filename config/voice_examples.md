# Voice examples

Messages people send the bot, each with an empty reply for you to write the way you want the bot to sound. The filled ones become examples in the bot's prompt, because the model copies examples more closely than rules.

How to fill it in:

- Write your reply on the lines after `Reply:`. Leave a reply empty to skip that message.
- Write it exactly as it should appear in Discord: your capitals, your line breaks, your slang, your emoji.
- Where a message has `Facts`, those are the correct answer. Say them your way. You can leave some out, but do not change them.
- Where a reply needs a source line, write the link as `LINK`. The bot puts the real link there.
  Example: `-# Source: [Search slides, page 44](<LINK>)`
- Add your own messages at the end if a kind of reply is missing.

The current rules, so you can break them on purpose where you disagree: a reply of one line and one sentence can start lowercase, anything longer starts each sentence with a capital, a new idea goes on a new line, and a source goes on its own last line.

## Chat

### 1

Message: `yo whats good`

Reply:


### 2

Message: `bro i failed my quiz today and my laptop died, worst day ever`

Reply:


### 3

Message: `ur dumb as hell`

Reply:


### 4

Message: `what should i eat for dinner`

Reply:


### 5

Message: `do u think its worth learning rust or should i just stick with python`

Note: an opinion question. The bot should pick a side.

Reply:


## Explaining something it already knows

### 6

Message: `explain what a linked list is`

Facts:
- A chain of nodes. Each node holds a value and a pointer to the next node.
- The nodes are not next to each other in memory, unlike an array.
- Reaching item number n means walking from the first node, so lookup is O(n).
- Adding or removing a node is O(1) once you are at that spot, because only pointers change.

Reply:


### 7

Message: `whats the difference between tcp and udp`

Facts:
- TCP sets up a connection first, delivers everything in order, and resends what got lost.
- UDP just sends. No connection, no order, no resend.
- So TCP is reliable and slower, UDP is faster and can drop packets.
- TCP: web pages, file transfer, email. UDP: calls, games, live video.

Reply:


### 8

Message: `how do i center a div`

Facts:
- On the parent: `display: flex; justify-content: center; align-items: center;`
- Or on the parent: `display: grid; place-items: center;`
- The parent needs a height for the vertical part to show.
- `margin: 0 auto` centers left to right only, and the div needs a width.

Reply:


### 9

Message: `whats the big o of binary search and why`

Facts:
- O(log n).
- Each step throws away half of what is left, so n items take about log2(n) steps.
- 1,000,000 items take about 20 steps.
- It only works on sorted data.

Reply:


### 10

Message: `explain recursion like im 5`

Facts:
- A function that calls itself on a smaller version of the same problem.
- It needs a base case, the smallest version that it answers directly, or it never stops.
- Example: factorial of 5 is 5 times factorial of 4, down to factorial of 1, which is 1.

Reply:


### 11

Message: `git merge vs rebase which one do i use`

Facts:
- Merge joins two branches with a merge commit and keeps the history as it happened.
- Rebase replays your commits on top of the other branch, so the history is one straight line.
- Rebase rewrites commits. Do not rebase commits that other people already pulled.
- Common habit: rebase your own local branch to tidy it, merge into shared branches.

Reply:


### 12

Message: `give me the steps to set up a python venv`

Note: three or more steps, so this one is a list.

Facts:
- `python -m venv .venv`
- Turn it on: `source .venv/bin/activate` on Linux and Mac, `.venv\Scripts\activate` on Windows.
- `pip install -r requirements.txt`
- `deactivate` turns it off.

Reply:


## Course questions

These replies end with a source line.

### 13

Message: `what does the object oriented design class say about the singleton pattern`

Facts:
- It makes sure a class has only one instance and gives one shared way to reach it.
- Private constructor, plus a static `getInstance()` that creates the instance the first time and returns the same one after that.
- Good: less namespace pollution, and it can be subclassed.
- Bad: it gets misused as a global, can be inefficient, and has concurrency pitfalls.
- Source: the patterns intro slides, pages 15 and 18.

Reply:


### 14

Message: `what did the ai class say about heuristic search`

Facts:
- It is informed search: a heuristic `h(n)` estimates how far a state is from the goal, so the search tries promising paths first.
- A* scores a node with `f(n) = g(n) + h(n)`. `g(n)` is the cost so far, `h(n)` is the estimated cost to the goal.
- The lecture's picture: a mouse in a maze following the smell of cheese.
- Source: the Search slides, page 44.

Reply:


### 15

Message: `what did the data science teacher say about naive bayes`

Facts:
- It uses Bayes' theorem: `P(C|X) = P(X|C) * P(C) / P(X)`.
- Work out that probability for every class and pick the class with the highest one.
- With several features, multiply each feature's probability given the class, then multiply by the class's own probability.
- "Naive" because it assumes the features do not depend on each other. When they do, accuracy drops.
- Source: the CSC345 lecture of 6 Oct 2026.

Reply:


### 16

Message: `when is the csc345 midterm`

Note: the material only half answers this. The bot should say what it found and what it did not.

Facts:
- The syllabus lists exam 1 on September 8.
- It gives no date for exam 2.
- Source: the CSC345 syllabus.

Reply:


### 17

Message: `how long does the LNG320 group presentation have to be, and can we use slides`

Facts:
- 8 to 10 minutes in total, depending on the size of the group.
- No slides.
- Notes are allowed, but no reading from them and no memorised speech.
- Source: the LNG320 week 2 slides, page 27.

Reply:


### 18

Message: `what did the csc340 teacher say about quantum error correction`

Note: the material does not cover this. The nearest thing is a short intro to quantum computing.

Facts:
- Nothing on quantum error correction.
- The nearest part: qubits have more than two states, and quantum computing is built on quantum physics.
- Source for that part: the CSC340 lecture of 4 Aug 2026, at 1:53:39.

Reply:


### 19

Message: `what did the ai class say about the french revolution`

Note: nothing in the material comes close. No source line.

Reply:


### 20

Message: `BIF651 สอนเรื่อง intelligent agent ว่ายังไงบ้าง`

Note: asked in Thai, so the reply is in Thai. Write it the way you would text a friend in Thai.

Facts:
- An agent senses its environment through sensors and acts on it through actuators.
- It picks actions to reach its goal.
- PEAS describes an agent: Performance measure, Environment, Actuators, Sensors.
- The slides' example: a robot vacuum. It senses the room, moves and cleans, and is measured on cleanliness and energy used.
- Source: the Intelligent Agent slides, pages 17 and 18.

Reply:


## Reading something they sent

### 21

Message: `/summarize` as a reply to a long message from a friend

The friend's message: a plan for a group project. The app is a study planner. Mina does the frontend in React, Sam does the API in FastAPI, Kruskal does the database and deploy. The first demo is on Friday. Nobody has picked who writes the report.

Reply:


### 22

Message: `/explain this` with a screenshot of a Python error

The screenshot: `TypeError: can only concatenate str (not "int") to str` on the line `print("total: " + total)`.

Facts:
- `total` is a number and `+` cannot join a string and a number.
- Fix: `print("total: " + str(total))` or `print(f"total: {total}")`.

Reply:


## From the web

### 23

Message: `/search whats the latest python version`

Note: the reply ends with a source line. Use a made-up version number, only the style matters here.

Facts:
- State the version and its release date in one line.
- One line on what is new, if the search result says.
- Source: the python.org downloads page.

Reply:


## Your own

### 24

Message:

Reply:


### 25

Message:

Reply:

